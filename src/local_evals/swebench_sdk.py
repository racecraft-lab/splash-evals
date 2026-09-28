"""One-shot local SDK transport; never executes agent commands or loads models."""

from __future__ import annotations

import json
import math
import os
import selectors
import signal
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, cast

MAX_OUTPUT = 65536
MAX_BYTES = 8 * 1024 * 1024


@dataclass
class OutputBudget:
    """Caller retains this per-task ledger; unknown consumption spends the reservation."""

    remaining: int
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def reserve(self) -> None:
        with self._lock:
            if type(self.remaining) is not int or self.remaining < MAX_OUTPUT:
                raise ValueError("insufficient output allowance")
            self.remaining -= MAX_OUTPUT

    def settle(self, charge: int) -> None:
        with self._lock:
            self.remaining += MAX_OUTPUT - charge


def _terminal(reason: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "error": reason,
        "choices": [],
        "usage": {"completion_tokens": None, "prompt_tokens": None, "total_tokens": None},
        "charged_output_tokens": MAX_OUTPUT,
    }


def _reap(process: subprocess.Popen[bytes]) -> bool:
    """Bound cleanup too; failure is terminal and cannot refund a reservation."""
    try:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # The child exited between poll and kill; wait still reaps it.
        process.wait(timeout=1.0)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _exchange(
    process: subprocess.Popen[bytes], payload: bytes, deadline: float, abort_grace: float
) -> tuple[bytes, bool]:
    """Bound all pipes without communicate() buffering unbounded child output."""
    output = bytearray()
    stderr_size = 0
    timed_out = False
    sent = 0
    with selectors.DefaultSelector() as selector:
        for stream, event in (
            (process.stdin, selectors.EVENT_WRITE),
            (process.stdout, selectors.EVENT_READ),
            (process.stderr, selectors.EVENT_READ),
        ):
            if stream is None:
                raise ValueError("missing child pipe")
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, event)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if timed_out:
                    raise TimeoutError("abort grace exhausted")
                timed_out = True
                process.send_signal(signal.SIGTERM)
                deadline = time.monotonic() + abort_grace
                continue
            for key, _ in selector.select(min(remaining, 0.1)):
                selected = cast(BinaryIO, key.fileobj)
                if selected is process.stdin:
                    try:
                        sent += os.write(selected.fileno(), payload[sent : sent + 65536])
                    except BrokenPipeError:
                        sent = len(payload)
                    if sent == len(payload):
                        selector.unregister(selected)
                        selected.close()
                    continue
                chunk = os.read(selected.fileno(), 65536)
                if not chunk:
                    selector.unregister(selected)
                    continue
                if selected is process.stdout:
                    output.extend(chunk)
                else:
                    stderr_size += len(chunk)  # Private diagnostics are discarded, never printed.
                if len(output) > MAX_BYTES or stderr_size > 65536:
                    raise ValueError("child output bound exceeded")
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
    return bytes(output), timed_out


def _checked_response(raw: bytes, alias: str, timed_out: bool) -> dict[str, Any]:
    response = json.loads(raw)
    if not isinstance(response, dict):
        raise ValueError("invalid child response")
    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("invalid child usage")
    actual = usage.get("completion_tokens")
    if actual is not None and (type(actual) is not int or not 0 <= actual <= MAX_OUTPUT):
        raise ValueError("invalid child usage")
    prompt = usage.get("prompt_tokens")
    if prompt is not None and (type(prompt) is not int or prompt < 0):
        raise ValueError("invalid child usage")
    total = usage.get("total_tokens")
    if total is not None and (
        type(total) is not int
        or prompt is None
        or actual is None
        or total != prompt + actual
    ):
        raise ValueError("invalid child usage")
    charge = MAX_OUTPUT if actual is None else actual
    if response.get("charged_output_tokens") != charge:
        raise ValueError("invalid child charge")
    if response.get("status") not in {"completed", "cancelled"}:
        return _terminal("sdk_transport_refused")
    expected_evidence = {
        "model_key": "qwen3.8-27b-splash",
        "device_identifier": None,
        "format": "yuzu",
        "temperature": 1,
        "top_p": 0.95,
        "max_output_tokens": MAX_OUTPUT,
        "reasoning": "on",
        "context_length": 131072,
        "transport": "lmstudio-sdk-2.0.0",
    }
    evidence = response.get("evidence")
    if (
        not isinstance(evidence, dict)
        or not set(expected_evidence).issubset(evidence)
        or set(evidence) - (set(expected_evidence) | {"stop_reason"})
        or any(evidence.get(key) != value for key, value in expected_evidence.items())
    ):
        raise ValueError("unverified child response")
    stop_reason = evidence.get("stop_reason")
    if "stop_reason" in evidence and stop_reason not in {
        None,
        "eosFound",
        "maxPredictedTokensReached",
        "contextLengthReached",
        "userStopped",
    }:
        raise ValueError("unverified child response")
    if response["status"] == "completed" and "stop_reason" in evidence and stop_reason not in {
        "eosFound",
        "maxPredictedTokensReached",
        "contextLengthReached",
    }:
        raise ValueError("unverified child response")
    choices = response.get("choices")
    if response.get("model") != alias:
        raise ValueError("unverified child response")
    if timed_out or response["status"] == "cancelled":
        refused = _terminal("request_cancelled")
        refused["status"] = "cancelled"
        refused["evidence"] = dict(evidence)
        return refused
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("invalid child choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if (
        not isinstance(message, dict)
        or message.get("role") != "assistant"
        or not isinstance(message.get("content"), str)
    ):
        raise ValueError("invalid child content")
    if "stop_reason" in evidence:
        finish_reason = choices[0].get("finish_reason")
        expected_finish_reason = {
            "eosFound": "stop",
            "maxPredictedTokensReached": "length",
            "contextLengthReached": "length",
        }.get(stop_reason)
        if finish_reason != expected_finish_reason:
            raise ValueError("unverified child completion reason")
    return response


def sdk_chat_response(
    body: Mapping[str, Any],
    *,
    budget: OutputBudget,
    deadline: float,
    node: Path,
    transport: Path,
    abort_grace: float = 2.0,
) -> dict[str, Any]:
    """Host lm_chat seam. Terminal results have no choices and cannot become commands.

    Caller must persist reservation/response alongside its existing task checkpoint.
    No HTTP equivalence or total-token budget is asserted.
    """
    if not math.isfinite(deadline) or deadline <= time.monotonic() or not 0 < abort_grace <= 5:
        raise ValueError("invalid transport deadline")
    if not node.is_absolute() or not transport.is_absolute():
        raise ValueError("transport paths must be absolute")
    payload = json.dumps(
        {"endpoint": "ws://127.0.0.1:1234", "body": body}, ensure_ascii=False, allow_nan=False
    ).encode()
    if len(payload) > MAX_BYTES:
        raise ValueError("request bytes exceeded")
    budget.reserve()
    process = None
    response = _terminal("sdk_transport_failed")
    try:
        # No inherited credentials, proxy settings, NODE_OPTIONS, or plugin variables.
        process = subprocess.Popen(  # noqa: S603 - trusted absolute Node/script paths, no shell.
            [str(node), str(transport)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            start_new_session=True,
        )
        raw, timed_out = _exchange(process, payload, deadline, abort_grace)
        response = _checked_response(raw, str(body.get("model", "")), timed_out)
        if process.returncode != 0 and response.get("status") == "completed":
            response = _terminal("child_exit_mismatch")
    except (OSError, ValueError, TypeError, KeyError, TimeoutError, subprocess.TimeoutExpired):
        response = _terminal("sdk_transport_failed")
    except BaseException:
        response = _terminal("sdk_transport_interrupted")
        raise
    finally:
        try:
            if process is not None and not _reap(process):
                response = _terminal("child_cleanup_unverified")
        except BaseException:
            response = _terminal("child_cleanup_interrupted")
            raise
        finally:
            budget.settle(response["charged_output_tokens"])
    return response
