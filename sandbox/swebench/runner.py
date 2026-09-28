#!/usr/bin/env python3
"""Pinned mini-SWE-agent control plane using a narrow host RPC mediator."""

from __future__ import annotations

import base64
import functools
import hashlib
import importlib
import importlib.metadata
import importlib.resources
import json
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from minisweagent import __version__ as mini_version
from minisweagent.agents.default import DefaultAgent
from minisweagent.exceptions import Submitted
from minisweagent.models.utils.actions_text import (
    format_observation_messages,
    parse_regex_actions,
)

_STABLE_JSON_ENCODER = json.JSONEncoder(sort_keys=True, separators=(",", ":"))
_EXPECTED_DISTRIBUTIONS = {
    "mini-swe-agent": "2.4.6",
    "swebench": "5.0.2",
}


def _effective_agent_config(config: object, request: object) -> dict[str, Any]:
    if not isinstance(config, dict) or not isinstance(config.get("agent"), dict):
        raise RuntimeError("control-plane agent configuration is invalid")
    if not isinstance(request, dict):
        raise RuntimeError("control-plane request is invalid")
    max_turns = request.get("max_turns")
    max_requests = request.get("max_requests")
    timeout_seconds = request.get("timeout_seconds")
    if type(max_turns) is not int or not 1 <= max_turns <= 100:
        raise RuntimeError("profile step limit is invalid")
    if type(max_requests) is not int or not 1 <= max_requests <= 100:
        raise RuntimeError("profile request limit is invalid")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise RuntimeError("profile wall-time limit is invalid")
    agent_config = dict(config["agent"])
    agent_config.pop("tools", None)
    templates = _upstream_templates()
    agent_config["system_template"] = templates["system_template"]
    agent_config["instance_template"] = templates["instance_template"]
    agent_config["step_limit"] = max_turns
    agent_config["wall_time_limit_seconds"] = timeout_seconds
    return agent_config


@functools.cache
def _upstream_templates() -> dict[str, str]:
    """Prompts and feedback from the pinned upstream text-action SWE-bench config."""
    source = importlib.resources.files("minisweagent.config").joinpath(
        "benchmarks/swebench_backticks.yaml"
    )
    config = yaml.safe_load(source.read_text())
    templates = {
        "system_template": config["agent"]["system_template"],
        "instance_template": config["agent"]["instance_template"],
        "observation_template": config["model"]["observation_template"],
        "format_error_template": config["model"]["format_error_template"],
    }
    if any(not isinstance(value, str) or not value for value in templates.values()):
        raise RuntimeError("pinned upstream templates are unavailable")
    return templates


def _canonical(value: object) -> bytes:
    return _STABLE_JSON_ENCODER.encode(value).encode()


def _rpc(value: dict[str, object]) -> dict[str, Any]:
    sys.stdout.buffer.write(_canonical(value) + b"\n")
    sys.stdout.buffer.flush()
    raw = sys.stdin.buffer.readline(8_388_609)
    if not raw or len(raw) > 8_388_608:
        raise RuntimeError("host mediator response is unavailable")
    response = json.loads(raw)
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise RuntimeError("host mediator refused the operation")
    return response


def _without_remaining_ttl(instance_record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in instance_record.items()
        if key != "remaining_ttl_seconds"
    }


def _loaded_instance(model: str) -> tuple[str, str]:
    payload = _rpc({"op": "lm_models"})["value"]
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for candidate in payload.get("models", []):
        if not isinstance(candidate, dict):
            continue
        matches.extend(
            (candidate, instance)
            for instance in candidate.get("loaded_instances", [])
            if isinstance(instance, dict)
            and (candidate.get("key") == model or instance.get("id") == model)
        )
    if len(matches) != 1 or not isinstance(matches[0][1].get("id"), str):
        raise RuntimeError("frozen model does not have exactly one loaded instance")
    model_record = dict(matches[0][0])
    loaded_instances = model_record.get("loaded_instances")
    if isinstance(loaded_instances, list):
        model_record["loaded_instances"] = [
            _without_remaining_ttl(instance)
            if isinstance(instance, dict)
            else instance
            for instance in loaded_instances
        ]
    fingerprint = hashlib.sha256(
        _canonical(
            {
                "model": model_record,
                "instance": _without_remaining_ttl(matches[0][1]),
            }
        )
    ).hexdigest()
    return matches[0][1]["id"], fingerprint


class StrictModel:
    """mini-SWE-agent model port with no direct network capability."""

    _ACTION = re.compile(r"```mswea_bash_command\s*\n(.*?)\n```", re.DOTALL)

    def __init__(self, request: dict[str, Any]) -> None:
        self.request = request
        self.config = SimpleNamespace(model_name=request["model"])
        self.verified_responses = 0
        self.output_tokens = 0
        self.prompt_token_counts: list[int | None] = []
        self.finish_reason: str | None = None
        self.context_length: int | None = None
        self.truncation_status: str | None = None
        # Only a direct SDK stop reason can establish context-limit truncation.
        self.context_truncation_status: str | None = None
        self.trajectory_elapsed_seconds: float | None = None

    def query(self, messages: list[dict[str, Any]], **_kwargs: object) -> dict[str, Any]:
        if self.verified_responses >= self.request["max_requests"]:
            raise RuntimeError("request budget exhausted")
        before_id, before_fingerprint = _loaded_instance(self.request["model"])
        if before_fingerprint != self.request["served_model_fingerprint"]:
            raise RuntimeError("served model fingerprint drifted before response")
        parameters = self.request["parameters"]
        response = _rpc(
            {
                "op": "lm_chat",
                "body": {
                    "model": self.request["model"],
                    "messages": [
                        {
                            "role": item.get("role", "user"),
                            "content": str(item.get("content", "")),
                        }
                        for item in messages
                        if item.get("role") in {"system", "user", "assistant"}
                    ],
                    "stream": False,
                    "temperature": parameters["temperature"],
                    "top_p": parameters["top_p"],
                    "max_tokens": parameters["max_output_tokens"],
                    "reasoning_effort": parameters["reasoning_effort"],
                },
            }
        )["value"]
        after_id, after_fingerprint = _loaded_instance(self.request["model"])
        choices = response.get("choices")
        if (
            before_id != after_id
            or before_fingerprint != after_fingerprint
            or response.get("model") not in {self.request["model"], before_id}
            or not isinstance(choices, list)
            or len(choices) != 1
        ):
            raise RuntimeError("LM Studio did not prove the exact served instance")
        choice = choices[0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise RuntimeError("LM Studio completion shape is invalid")
        content = choice["message"].get("content")
        # A verified model reply is an attempt even when its proposed action is invalid.
        self.verified_responses += 1
        usage = response.get("usage", {})
        if isinstance(usage, dict) and isinstance(usage.get("completion_tokens"), int):
            self.output_tokens += usage["completion_tokens"]
        prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        self.prompt_token_counts.append(
            prompt_tokens if type(prompt_tokens) is int and prompt_tokens >= 0 else None
        )
        finish_reason = choice.get("finish_reason")
        self.finish_reason = finish_reason if isinstance(finish_reason, str) else None
        evidence = response.get("evidence")
        context_length = evidence.get("context_length") if isinstance(evidence, dict) else None
        self.context_length = (
            context_length if type(context_length) is int and context_length >= 0 else None
        )
        stop_reason = evidence.get("stop_reason") if isinstance(evidence, dict) else None
        self.context_truncation_status = (
            "context_length_reached" if stop_reason == "contextLengthReached" else None
        )
        self.truncation_status = {
            "eosFound": "none",
            "maxPredictedTokensReached": "generation_limit_reached",
            "contextLengthReached": "context_length_reached",
        }.get(stop_reason)
        if stop_reason is None:
            self.truncation_status = {
                "stop": "none",
                "length": "limit_reached_unspecified",
            }.get(self.finish_reason)
        if not isinstance(content, str):
            raise RuntimeError("LM Studio completion content is invalid")
        # As in pinned upstream 2.4.6, a malformed reply raises a recoverable FormatError.
        actions = parse_regex_actions(
            content,
            action_regex=self._ACTION.pattern,
            format_error_template=_upstream_templates()["format_error_template"],
            template_kwargs={"finish_reason": self.finish_reason},
        )
        return {
            "role": "assistant",
            "content": content,
            "extra": {"actions": actions, "cost": 0.0},
        }

    def format_message(self, **kwargs: object) -> dict[str, object]:
        return dict(kwargs)

    def format_observation_messages(
        self,
        _message: dict[str, Any],
        outputs: list[dict[str, Any]],
        template_vars: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return format_observation_messages(
            outputs,
            observation_template=_upstream_templates()["observation_template"],
            template_vars=template_vars,
        )

    def get_template_vars(self, **_kwargs: object) -> dict[str, object]:
        return {"model_name": self.request["model"]}

    def serialize(self) -> dict[str, object]:
        return {"info": {"model": self.request["model"], "mini_version": mini_version}}


class StrictContainerEnvironment:
    """Environment port; host mediator executes only inside the task container."""

    def __init__(self) -> None:
        self.config = SimpleNamespace(cwd="/testbed")

    def execute(self, action: dict[str, Any], cwd: str = "") -> dict[str, Any]:
        command = action.get("command")
        if not isinstance(command, str) or cwd not in {"", "/testbed"}:
            raise RuntimeError("non-bash or unsafe task action refused")
        response = _rpc({"op": "task_exec", "command": command})
        output = base64.b64decode(response["output_b64"], validate=True).decode(errors="replace")
        returncode = response["returncode"]
        # As in pinned upstream DockerEnvironment, the text after the marker line is the
        # submission, and it is the prediction that gets graded.
        lines = output.lstrip().splitlines(keepends=True)
        marker = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
        if lines and lines[0].strip() == marker and returncode == 0:
            submission = "".join(lines[1:])
            raise Submitted(
                {
                    "role": "exit",
                    "content": submission,
                    "extra": {"exit_status": "Submitted", "submission": submission},
                }
            )
        return {"output": output, "returncode": returncode, "exception_info": ""}

    def get_template_vars(self, **_kwargs: object) -> dict[str, object]:
        return {"cwd": "/testbed"}

    def serialize(self) -> dict[str, object]:
        return {"info": {"environment": "racecraft-offline-docker-exec"}}


def _write_evidence(filename: str, data: bytes) -> None:
    _rpc(
        {
            "op": "write_evidence",
            "filename": filename,
            "data_b64": base64.b64encode(data).decode(),
        }
    )


def _smoke_check() -> int:
    for distribution, expected in _EXPECTED_DISTRIBUTIONS.items():
        if importlib.metadata.version(distribution) != expected:
            raise RuntimeError(f"{distribution} control-plane version mismatch")
    importlib.import_module("swebench")
    config = yaml.safe_load(Path("/opt/racecraft/mini-swe-agent.yaml").read_text())
    if not isinstance(config, dict) or config.get("mini_swe_agent_version") != mini_version:
        raise RuntimeError("mini-SWE-agent configuration version mismatch")
    agent = config.get("agent")
    if not isinstance(agent, dict) or agent.get("tools") != ["bash"]:
        raise RuntimeError("control-plane configuration is not bash-only")
    _upstream_templates()
    sys.stdout.buffer.write(
        _canonical({"status": "ready", "versions": _EXPECTED_DISTRIBUTIONS}) + b"\n"
    )
    sys.stdout.buffer.flush()
    return 0


def main() -> int:
    envelope = json.loads(sys.stdin.buffer.readline(1_048_577))
    if not isinstance(envelope, dict) or envelope.get("op") != "start":
        raise RuntimeError("control-plane start envelope is invalid")
    request = envelope["request"]
    config = yaml.safe_load(Path("/opt/racecraft/mini-swe-agent.yaml").read_text())
    agent_config = _effective_agent_config(config, request)
    model = StrictModel(request)
    trajectory: dict[str, Any] = {"schema_version": 1, "messages": []}
    status = "infrastructure_error"
    try:
        task_data = base64.b64decode(_rpc({"op": "task_json"})["output_b64"], validate=True)
        task = json.loads(task_data)
        statement = task.get("problem_statement")
        if task.get("instance_id") != request["instance_id"]:
            raise RuntimeError("task image instance identity mismatch")
        if (
            not isinstance(statement, str)
            or hashlib.sha256(statement.encode()).hexdigest() != request["problem_statement_sha256"]
        ):
            raise RuntimeError("task image problem statement mismatch")
        agent = DefaultAgent(model, StrictContainerEnvironment(), **agent_config)
        try:
            trajectory_started = time.monotonic()
            try:
                agent_result = agent.run(statement)
            finally:
                elapsed = time.monotonic() - trajectory_started
                if elapsed >= 0:
                    model.trajectory_elapsed_seconds = elapsed
        finally:
            trajectory = agent.serialize()
        exit_status = (
            agent_result.get("exit_status")
            if isinstance(agent_result, dict)
            else None
        )
        if type(exit_status) is str and exit_status == "Submitted":
            status = "completed"
        else:
            status = "model_failure" if model.verified_responses else "infrastructure_error"
    except Exception:
        status = "model_failure" if model.verified_responses else "infrastructure_error"

    trajectory_bytes = _canonical(trajectory)
    trajectory_sha = hashlib.sha256(trajectory_bytes).hexdigest()
    _write_evidence(f"trajectory-{trajectory_sha}.json", trajectory_bytes)
    patch_sha: str | None = None
    handle: str | None = None
    if status == "completed":
        submission = agent_result.get("submission") if isinstance(agent_result, dict) else None
        patch = submission.encode() if isinstance(submission, str) else b""
        if not patch.strip():
            status = "model_failure"
        else:
            patch_sha = hashlib.sha256(patch).hexdigest()
            filename = f"prediction-{patch_sha}.patch"
            _write_evidence(filename, patch)
            handle = f"private://{filename}"
    result = {
        "status": status,
        "attempted": model.verified_responses > 0,
        "first_verified_response": model.verified_responses > 0,
        "turn_count": model.verified_responses,
        "request_count": model.verified_responses,
        "output_tokens": model.output_tokens,
        "prompt_tokens": (
            sum(model.prompt_token_counts)
            if model.prompt_token_counts
            and all(value is not None for value in model.prompt_token_counts)
            else None
        ),
        "finish_reason": model.finish_reason,
        "context_length": model.context_length,
        "context_truncation_status": model.context_truncation_status,
        "truncation_status": model.truncation_status,
        "trajectory_elapsed_seconds": model.trajectory_elapsed_seconds,
        "locality_checks": model.verified_responses,
        "bash_actions_only": True,
        "effective_step_limit": agent_config["step_limit"],
        "effective_wall_time_limit_seconds": agent_config["wall_time_limit_seconds"],
        "trajectory_sha256": trajectory_sha,
        "model_patch_sha256": patch_sha,
        "prediction_handle": handle,
    }
    sys.stdout.buffer.write(_canonical({"op": "result", "value": result}) + b"\n")
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--smoke-check"]:
        raise SystemExit(_smoke_check())
    if sys.argv[1:]:
        raise RuntimeError("unsupported control-plane argument")
    raise SystemExit(main())
