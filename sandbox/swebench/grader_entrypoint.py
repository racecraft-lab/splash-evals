#!/usr/bin/env python3
"""Apply one pinned SWE-bench patch inside the grader container.

The host launcher owns and attests the container boundary. This entrypoint does
not inspect or claim isolation; it only consumes a bounded, digest-checked
request, verifies the supplied TestSpec digest in memory, applies the patch with the pinned
SWE-bench commands, then stops. The host owns any later eval invocation and
scoring. This entrypoint emits bounded patch-setup output, never an eval result.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import selectors
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final

SCHEMA_VERSION: Final = 1
SCRATCH_ROOT: Final = Path("/tmp/racecraft-swebench")  # noqa: S108 - host-approved tmpfs path.
INPUT_DIR_NAME: Final = "input"
PATCH_FILENAME: Final = "candidate.patch"
TESTBED: Final = Path("/testbed")
# Root-owned `git status --porcelain --untracked-files=all` of the pristine image testbed,
# recorded at build time. Some official images ship untracked files (for example build/).
TESTBED_BASELINE: Final = Path("/opt/racecraft/testbed-baseline.txt")
BASELINE_OWNER_UID: Final = 0

MAX_REQUEST_BYTES: Final = 92 * 1024 * 1024
MAX_PATCH_BYTES: Final = 64 * 1024 * 1024
MAX_EVAL_SCRIPT_BYTES: Final = 4 * 1024 * 1024
MAX_TIMEOUT_SECONDS: Final = 24 * 60 * 60
MAX_OUTPUT_BYTES: Final = 64 * 1024 * 1024
CONTROL_OUTPUT_BYTES: Final = 1024 * 1024
CONTROL_TIMEOUT_SECONDS: Final = 30.0
PATCH_ATTEMPT_TIMEOUT_SECONDS: Final = 120.0
REAP_TIMEOUT_SECONDS: Final = 2.0

EXIT_OK: Final = 0
EXIT_INVALID_INPUT: Final = 2
EXIT_INPUT_TOO_LARGE: Final = 3
EXIT_UNSAFE_INPUT: Final = 4
EXIT_DIGEST_MISMATCH: Final = 5
EXIT_TESTBED_INVALID: Final = 6
EXIT_PATCH_FAILED: Final = 7
EXIT_TIMEOUT: Final = 8
EXIT_OUTPUT_LIMIT: Final = 9
EXIT_LAUNCH_FAILED: Final = 10
EXIT_RESET_FAILED: Final = 11
EXIT_INTERNAL: Final = 70

_REQUEST_KEYS: Final = frozenset(
    {
        "schema_version",
        "patch_b64",
        "eval_script_b64",
        "expected_patch_sha256",
        "expected_eval_script_sha256",
        "timeout_seconds",
        "max_output_bytes",
    }
)
_SHA256_HEX_LENGTH: Final = 64
PHASE_PATCH_READY: Final = "patch_ready"
PHASE_APPLY_FAILED: Final = "apply_failed"


class EntryFailure(Exception):
    """A bounded failure suitable for the machine-readable result envelope."""

    def __init__(
        self,
        error: str,
        exit_code: int,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        timed_out: bool = False,
        patch_sha256: str | None = None,
        eval_script_sha256: str | None = None,
    ) -> None:
        super().__init__(error)
        self.error = error
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.patch_sha256 = patch_sha256
        self.eval_script_sha256 = eval_script_sha256


class ProcessLaunchFailure(Exception):
    """A child process could not be created or reaped."""


@dataclass(frozen=True)
class Request:
    patch: bytes
    eval_script: bytes
    expected_patch_sha256: str
    expected_eval_script_sha256: str
    timeout_seconds: int
    max_output_bytes: int


@dataclass(frozen=True)
class ProcessCapture:
    stdout: bytes
    stderr: bytes
    exit_code: int | None
    timed_out: bool
    output_exceeded: bool


def _result(
    *,
    phase: str = PHASE_APPLY_FAILED,
    process_exit_code: int,
    eval_exit_code: int | None = None,
    timed_out: bool = False,
    patch_sha256: str | None = None,
    eval_script_sha256: str | None = None,
    stdout: bytes = b"",
    stderr: bytes = b"",
    error: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": phase,
        "process_exit_code": process_exit_code,
        "eval_exit_code": eval_exit_code,
        "timed_out": timed_out,
        "patch_sha256": patch_sha256,
        "eval_script_sha256": eval_script_sha256,
        "stdout_b64": base64.b64encode(stdout).decode("ascii"),
        "stderr_b64": base64.b64encode(stderr).decode("ascii"),
        "error": error,
    }


def _pairs_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate JSON key")
    return value


def _decode_base64(value: object, *, maximum: int) -> bytes:
    if not isinstance(value, str):
        raise EntryFailure("invalid_input", EXIT_INVALID_INPUT)
    # Bound encoded size before allocating the decoded buffer.
    encoded_maximum = 4 * ((maximum + 2) // 3)
    if len(value) > encoded_maximum:
        raise EntryFailure("input_too_large", EXIT_INPUT_TOO_LARGE)
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise EntryFailure("invalid_input", EXIT_INVALID_INPUT) from None
    if len(decoded) > maximum:
        raise EntryFailure("input_too_large", EXIT_INPUT_TOO_LARGE)
    return decoded


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != _SHA256_HEX_LENGTH:
        return False
    return all(character in "0123456789abcdef" for character in value)


def _parse_request(raw: bytes) -> Request:
    if len(raw) > MAX_REQUEST_BYTES:
        raise EntryFailure("input_too_large", EXIT_INPUT_TOO_LARGE)
    try:
        decoded = raw.decode("utf-8", errors="strict")
        value = json.loads(decoded, object_pairs_hook=_pairs_without_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise EntryFailure("invalid_input", EXIT_INVALID_INPUT) from None
    if not isinstance(value, dict) or frozenset(value) != _REQUEST_KEYS:
        raise EntryFailure("invalid_input", EXIT_INVALID_INPUT)
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise EntryFailure("invalid_input", EXIT_INVALID_INPUT)

    expected_patch_sha256 = value["expected_patch_sha256"]
    expected_eval_sha256 = value["expected_eval_script_sha256"]
    if not _is_sha256(expected_patch_sha256) or not _is_sha256(expected_eval_sha256):
        raise EntryFailure("invalid_input", EXIT_INVALID_INPUT)

    timeout_seconds = value["timeout_seconds"]
    max_output_bytes = value["max_output_bytes"]
    if (
        type(timeout_seconds) is not int
        or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS
        or type(max_output_bytes) is not int
        or not 1 <= max_output_bytes <= MAX_OUTPUT_BYTES
    ):
        raise EntryFailure("invalid_input", EXIT_INVALID_INPUT)

    patch = _decode_base64(value["patch_b64"], maximum=MAX_PATCH_BYTES)
    eval_script = _decode_base64(value["eval_script_b64"], maximum=MAX_EVAL_SCRIPT_BYTES)
    if b"\x00" in eval_script:
        raise EntryFailure("unsafe_input", EXIT_UNSAFE_INPUT)
    if not hmac.compare_digest(hashlib.sha256(patch).hexdigest(), expected_patch_sha256):
        raise EntryFailure("digest_mismatch", EXIT_DIGEST_MISMATCH)
    if not hmac.compare_digest(hashlib.sha256(eval_script).hexdigest(), expected_eval_sha256):
        raise EntryFailure("digest_mismatch", EXIT_DIGEST_MISMATCH)
    return Request(
        patch=patch,
        eval_script=eval_script,
        expected_patch_sha256=expected_patch_sha256,
        expected_eval_script_sha256=expected_eval_sha256,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )


def _write_exclusive(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise EntryFailure("unsafe_input", EXIT_UNSAFE_INPUT)
            view = view[written:]
    except EntryFailure:
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
            try:
                path.unlink()
            except OSError:
                pass
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
            try:
                path.unlink()
            except OSError:
                pass
        raise EntryFailure("unsafe_input", EXIT_UNSAFE_INPUT) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_regular_file(path: Path, *, maximum: int) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise EntryFailure("unsafe_input", EXIT_UNSAFE_INPUT) from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise EntryFailure("unsafe_input", EXIT_UNSAFE_INPUT)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(maximum + 1)
    except OSError:
        raise EntryFailure("unsafe_input", EXIT_UNSAFE_INPUT) from None
    finally:
        os.close(descriptor)
    if len(content) > maximum:
        raise EntryFailure("input_too_large", EXIT_INPUT_TOO_LARGE)
    return content


def _stage_and_verify(request: Request) -> tuple[Path, str, str]:
    root = SCRATCH_ROOT
    input_dir = root / INPUT_DIR_NAME
    patch_path = input_dir / PATCH_FILENAME
    root_created = False
    input_created = False
    patch_created = False
    staged = False
    eval_sha256 = hashlib.sha256(request.eval_script).hexdigest()
    if not hmac.compare_digest(eval_sha256, request.expected_eval_script_sha256):
        raise EntryFailure(
            "digest_mismatch",
            EXIT_DIGEST_MISMATCH,
            eval_script_sha256=eval_sha256,
        )
    try:
        os.mkdir(root, 0o700)
        root_created = True
        os.mkdir(input_dir, 0o700)
        input_created = True
        _write_exclusive(patch_path, request.patch)
        patch_created = True
        patch_bytes = _read_regular_file(patch_path, maximum=MAX_PATCH_BYTES)
        patch_sha256 = hashlib.sha256(patch_bytes).hexdigest()
        if not hmac.compare_digest(patch_sha256, request.expected_patch_sha256):
            raise EntryFailure(
                "digest_mismatch",
                EXIT_DIGEST_MISMATCH,
                patch_sha256=patch_sha256,
                eval_script_sha256=eval_sha256,
            )
        staged = True
        return patch_path, patch_sha256, eval_sha256
    except EntryFailure:
        raise
    except OSError:
        raise EntryFailure("unsafe_input", EXIT_UNSAFE_INPUT) from None
    finally:
        if not staged:
            _remove_owned_input_tree(
                root_created=root_created,
                input_created=input_created,
                patch_created=patch_created,
            )


def _pristine_status(*, after_clean: bool) -> bytes:
    """Return the expected status: the build-time baseline, less untracked entries after clean."""
    try:
        metadata = TESTBED_BASELINE.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != BASELINE_OWNER_UID
            or metadata.st_mode & 0o222
            or metadata.st_size > CONTROL_OUTPUT_BYTES
        ):
            raise EntryFailure("testbed_invalid", EXIT_TESTBED_INVALID)
        baseline = TESTBED_BASELINE.read_bytes()
    except OSError:
        raise EntryFailure("testbed_invalid", EXIT_TESTBED_INVALID) from None
    if not after_clean:
        return baseline
    # `git clean -fd` removes untracked files, so only tracked entries may remain.
    return b"".join(
        line for line in baseline.splitlines(keepends=True) if not line.startswith(b"?? ")
    )


def _verify_fresh_testbed(deadline: float, *, after_clean: bool = False) -> None:
    expected = _pristine_status(after_clean=after_clean)
    try:
        metadata = TESTBED.lstat()
    except OSError:
        raise EntryFailure("testbed_invalid", EXIT_TESTBED_INVALID) from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise EntryFailure("testbed_invalid", EXIT_TESTBED_INVALID)
    try:
        git_metadata = (TESTBED / ".git").lstat()
    except OSError:
        raise EntryFailure("testbed_invalid", EXIT_TESTBED_INVALID) from None
    if not (stat.S_ISDIR(git_metadata.st_mode) or stat.S_ISREG(git_metadata.st_mode)):
        raise EntryFailure("testbed_invalid", EXIT_TESTBED_INVALID)
    capture = _run_bounded(
        ("git", "status", "--porcelain", "--untracked-files=all"),
        cwd=TESTBED,
        deadline=min(deadline, time.monotonic() + CONTROL_TIMEOUT_SECONDS),
        max_output_bytes=CONTROL_OUTPUT_BYTES,
    )
    if capture.timed_out or capture.output_exceeded or capture.exit_code != 0:
        raise EntryFailure("testbed_invalid", EXIT_TESTBED_INVALID)
    if capture.stderr or capture.stdout != expected:
        # The checkout must match the pristine image testbed recorded at build time.
        raise EntryFailure("testbed_invalid", EXIT_TESTBED_INVALID)


def _run_control_command(
    args: Sequence[str],
    *,
    deadline: float,
    error: str,
    exit_code: int,
) -> ProcessCapture:
    capture = _run_bounded(
        args,
        cwd=TESTBED,
        deadline=min(deadline, time.monotonic() + CONTROL_TIMEOUT_SECONDS),
        max_output_bytes=CONTROL_OUTPUT_BYTES,
    )
    if capture.timed_out:
        raise EntryFailure(error, exit_code, timed_out=True)
    if capture.output_exceeded or capture.exit_code != 0:
        raise EntryFailure(error, exit_code)
    return capture


def _reset_testbed(deadline: float) -> None:
    """Restore HEAD and clean untracked files before a pinned fallback attempt."""
    # Pinned SWE-bench includes `git apply --3way`, which implies --index and
    # may leave unmerged index stages. Restore both index and worktree, not just
    # the worktree, before running the next patch mode.
    _run_control_command(
        ("git", "reset", "--hard", "HEAD"),
        deadline=deadline,
        error="reset_failed",
        exit_code=EXIT_RESET_FAILED,
    )
    _run_control_command(
        ("git", "clean", "-fd"),
        deadline=deadline,
        error="reset_failed",
        exit_code=EXIT_RESET_FAILED,
    )
    try:
        _verify_fresh_testbed(deadline, after_clean=True)
    except EntryFailure as failure:
        raise EntryFailure(
            "reset_failed",
            EXIT_RESET_FAILED,
            timed_out=failure.timed_out,
        ) from None


def _apply_official_patch(
    patch_path: Path,
    *,
    deadline: float,
    max_output_bytes: int,
    patch_sha256: str,
    eval_script_sha256: str,
) -> ProcessCapture:
    """Use the exact ordered patch and reverse-check modes pinned in SWE-bench 5.0.2."""
    commands: tuple[tuple[str, ...], ...] = (
        ("git", "apply", "--verbose"),
        ("git", "apply", "--verbose", "--3way"),
        ("git", "apply", "--verbose", "--reject"),
        ("patch", "--batch", "--forward", "--fuzz=5", "-p1", "-i"),
    )
    last_capture: ProcessCapture | None = None
    for attempt, command in enumerate(commands):
        if attempt:
            _reset_testbed(deadline)
        command_deadline = min(
            deadline,
            time.monotonic() + PATCH_ATTEMPT_TIMEOUT_SECONDS,
        )
        capture = _run_bounded(
            (*command, str(patch_path)),
            cwd=TESTBED,
            deadline=command_deadline,
            max_output_bytes=max_output_bytes,
        )
        if capture.timed_out:
            raise EntryFailure(
                "timeout",
                EXIT_TIMEOUT,
                stdout=capture.stdout,
                stderr=capture.stderr,
                timed_out=True,
                patch_sha256=patch_sha256,
                eval_script_sha256=eval_script_sha256,
            )
        if capture.output_exceeded:
            raise EntryFailure(
                "output_limit",
                EXIT_OUTPUT_LIMIT,
                stdout=capture.stdout,
                stderr=capture.stderr,
                patch_sha256=patch_sha256,
                eval_script_sha256=eval_script_sha256,
            )
        if capture.exit_code == 0:
            return capture
        last_capture = capture

    # The reverse probe can succeed against a partly applied tree. Only probe
    # the frozen baseline so it can accept a patch already present in HEAD.
    _reset_testbed(deadline)
    reverse_check = _run_bounded(
        ("git", "apply", "--check", "--reverse", str(patch_path)),
        cwd=TESTBED,
        deadline=min(deadline, time.monotonic() + CONTROL_TIMEOUT_SECONDS),
        max_output_bytes=max_output_bytes,
    )
    if reverse_check.timed_out:
        _reset_testbed(deadline)
        raise EntryFailure(
            "timeout",
            EXIT_TIMEOUT,
            stdout=reverse_check.stdout,
            stderr=reverse_check.stderr,
            timed_out=True,
            patch_sha256=patch_sha256,
            eval_script_sha256=eval_script_sha256,
        )
    if reverse_check.output_exceeded:
        _reset_testbed(deadline)
        raise EntryFailure(
            "output_limit",
            EXIT_OUTPUT_LIMIT,
            stdout=reverse_check.stdout,
            stderr=reverse_check.stderr,
            patch_sha256=patch_sha256,
            eval_script_sha256=eval_script_sha256,
        )
    if reverse_check.exit_code == 0:
        return reverse_check

    # The disposable tree has already been restored and verified before probing.
    failure_capture = last_capture or reverse_check
    raise EntryFailure(
        "patch_failed",
        EXIT_PATCH_FAILED,
        stdout=failure_capture.stdout,
        stderr=failure_capture.stderr,
        patch_sha256=patch_sha256,
        eval_script_sha256=eval_script_sha256,
    )


def _record_chunk(
    stream: object,
    chunk: bytes,
    *,
    stdout_buffer: bytearray,
    stderr_buffer: bytearray,
    maximum: int,
) -> bool:
    remaining = maximum - len(stdout_buffer) - len(stderr_buffer)
    destination = stdout_buffer if stream == "stdout" else stderr_buffer
    destination.extend(chunk[: max(0, remaining)])
    return len(chunk) > max(0, remaining)


def _signal_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        try:
            process.kill()
        except OSError:
            pass


def _reap(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=REAP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=REAP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise ProcessLaunchFailure("child process could not be reaped") from None


def _run_bounded(
    args: Sequence[str],
    *,
    cwd: Path,
    deadline: float,
    max_output_bytes: int,
    pass_fds: tuple[int, ...] = (),
    merge_stderr: bool = False,
) -> ProcessCapture:
    """Run one fixed-argv child, keeping combined stdout/stderr within a hard cap."""
    if deadline <= time.monotonic():
        return ProcessCapture(b"", b"", None, True, False)
    try:
        process = subprocess.Popen(  # noqa: S603 - no shell; callers supply fixed argv.
            args,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
            close_fds=True,
            pass_fds=pass_fds,
            start_new_session=True,
        )
    except OSError:
        raise ProcessLaunchFailure("child process could not start") from None
    if process.stdout is None or (not merge_stderr and process.stderr is None):
        _signal_process_group(process)
        _reap(process)
        raise ProcessLaunchFailure("child output pipes are unavailable")

    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    timed_out = False
    output_exceeded = False
    selector = selectors.DefaultSelector()
    try:
        streams = [("stdout", process.stdout)]
        if process.stderr is not None:
            streams.append(("stderr", process.stderr))
        for name, stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, data=name)

        while selector.get_map():
            # Do not wait on inherited pipe descriptors after the direct child exits.
            # Drain bytes already queued, then leave orphan cleanup to container teardown.
            if process.poll() is not None:
                for key in list(selector.get_map().values()):
                    stream = key.fileobj
                    while True:
                        try:
                            chunk = os.read(stream.fileno(), 65_536)
                        except BlockingIOError:
                            break
                        if not chunk:
                            break
                        output_exceeded = _record_chunk(
                            key.data,
                            chunk,
                            stdout_buffer=stdout_buffer,
                            stderr_buffer=stderr_buffer,
                            maximum=max_output_bytes,
                        )
                        if output_exceeded:
                            break
                    selector.unregister(stream)
                    stream.close()
                    if output_exceeded:
                        break
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            events = selector.select(min(remaining, 0.1))
            for key, _ in events:
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), 65_536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                output_exceeded = _record_chunk(
                    key.data,
                    chunk,
                    stdout_buffer=stdout_buffer,
                    stderr_buffer=stderr_buffer,
                    maximum=max_output_bytes,
                )
                if output_exceeded:
                    break
            if output_exceeded:
                break

        if timed_out or output_exceeded:
            _signal_process_group(process)
        else:
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
                _signal_process_group(process)
    except OSError:
        _signal_process_group(process)
        _reap(process)
        raise ProcessLaunchFailure("child process capture failed") from None
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    _reap(process)
    exit_code = None if timed_out or output_exceeded else process.returncode
    return ProcessCapture(
        stdout=bytes(stdout_buffer),
        stderr=bytes(stderr_buffer),
        exit_code=exit_code,
        timed_out=timed_out,
        output_exceeded=output_exceeded,
    )


def _remove_owned_input_tree(
    *,
    root_created: bool,
    input_created: bool,
    patch_created: bool,
) -> None:
    input_dir = SCRATCH_ROOT / INPUT_DIR_NAME
    if patch_created:
        path = input_dir / PATCH_FILENAME
        try:
            metadata = path.lstat()
            if stat.S_ISREG(metadata.st_mode):
                path.unlink()
        except OSError:
            pass
    if input_created:
        try:
            input_dir.rmdir()
        except OSError:
            pass
    if root_created:
        try:
            SCRATCH_ROOT.rmdir()
        except OSError:
            pass


def _run_request(request: Request) -> dict[str, object]:
    patch_sha256 = hashlib.sha256(request.patch).hexdigest()
    eval_sha256 = hashlib.sha256(request.eval_script).hexdigest()
    # The host enforces this same frozen per-task timeout for the whole launcher.
    # Setup and every pinned fallback share the host-frozen task deadline.
    deadline = time.monotonic() + request.timeout_seconds
    patch_path: Path | None = None
    staged = False
    try:
        _verify_fresh_testbed(deadline)
        patch_path, patch_sha256, eval_sha256 = _stage_and_verify(request)
        staged = True
        patch_capture = _apply_official_patch(
            patch_path,
            deadline=deadline,
            max_output_bytes=request.max_output_bytes,
            patch_sha256=patch_sha256,
            eval_script_sha256=eval_sha256,
        )

        _remove_owned_input_tree(
            root_created=True,
            input_created=True,
            patch_created=True,
        )
        staged = False
        return _result(
            phase=PHASE_PATCH_READY,
            process_exit_code=EXIT_OK,
            eval_exit_code=None,
            patch_sha256=patch_sha256,
            eval_script_sha256=eval_sha256,
            stdout=patch_capture.stdout,
            stderr=patch_capture.stderr,
        )
    except EntryFailure as failure:
        return _result(
            phase=PHASE_APPLY_FAILED,
            process_exit_code=failure.exit_code,
            timed_out=failure.timed_out,
            patch_sha256=failure.patch_sha256 or patch_sha256,
            eval_script_sha256=failure.eval_script_sha256 or eval_sha256,
            stdout=failure.stdout,
            stderr=failure.stderr,
            error=failure.error,
        )
    except ProcessLaunchFailure:
        return _result(
            phase=PHASE_APPLY_FAILED,
            process_exit_code=EXIT_LAUNCH_FAILED,
            patch_sha256=patch_sha256,
            eval_script_sha256=eval_sha256,
            error="launch_failed",
        )
    except Exception:
        return _result(
            phase=PHASE_APPLY_FAILED,
            process_exit_code=EXIT_INTERNAL,
            patch_sha256=patch_sha256,
            eval_script_sha256=eval_sha256,
            error="internal_error",
        )
    finally:
        if staged:
            _remove_owned_input_tree(
                root_created=True,
                input_created=True,
                patch_created=True,
            )


def main(
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
) -> int:
    input_stream = stdin if stdin is not None else sys.stdin.buffer
    output_stream = stdout if stdout is not None else sys.stdout.buffer
    try:
        raw = input_stream.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            result = _result(
                process_exit_code=EXIT_INPUT_TOO_LARGE,
                error="input_too_large",
            )
        else:
            request = _parse_request(raw)
            result = _run_request(request)
    except EntryFailure as failure:
        result = _result(
            process_exit_code=failure.exit_code,
            timed_out=failure.timed_out,
            patch_sha256=failure.patch_sha256,
            eval_script_sha256=failure.eval_script_sha256,
            stdout=failure.stdout,
            stderr=failure.stderr,
            error=failure.error,
        )
    except Exception:
        result = _result(process_exit_code=EXIT_INTERNAL, error="internal_error")
    output_stream.write(json.dumps(result, separators=(",", ":")).encode("utf-8") + b"\n")
    output_stream.flush()
    return int(result["process_exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
