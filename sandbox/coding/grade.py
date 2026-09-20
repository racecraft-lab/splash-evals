#!/usr/bin/env python3
"""Standard-library-only private grader entrypoint.

The outer Docker policy provides the security boundary.  This process adds
strict request validation, per-test subprocesses, resource limits, and a small
result protocol that never echoes candidate output.
"""

from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn

MAX_REQUEST_BYTES = 262_144
MAX_SOURCE_BYTES = 131_072
MAX_TESTS = 10
MAX_STDIN_BYTES = 16_384
MAX_EXPECTED_BYTES = 16_384
MAX_PROCESS_OUTPUT_BYTES = 65_536
MAX_TIMEOUT_SECONDS = 10.0
REQUEST_KEYS = frozenset({"schema_version", "source", "tests", "per_test_timeout_seconds"})
TEST_KEYS = frozenset({"name", "stdin", "expected_stdout"})


class ProtocolError(ValueError):
    """Raised for an invalid private grader request."""


def _fail(code: str) -> NoReturn:
    json.dump({"schema_version": 1, "passed": False, "error": code}, sys.stdout)
    sys.stdout.write("\n")
    raise SystemExit(2)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProtocolError(f"{label}_not_mapping")
    return value


def _bounded_text(value: Any, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ProtocolError(f"{label}_not_string")
    if len(value.encode("utf-8")) > limit:
        raise ProtocolError(f"{label}_too_large")
    return value


def _load_test(raw_test: Any, index: int, seen_names: set[str]) -> tuple[str, str, str]:
    test = _mapping(raw_test, f"test_{index}")
    if frozenset(test) != TEST_KEYS:
        raise ProtocolError("test_fields_invalid")
    name = _bounded_text(test["name"], "test_name", 128)
    if not name or name in seen_names:
        raise ProtocolError("test_name_invalid")
    seen_names.add(name)
    stdin = _bounded_text(test["stdin"], "test_stdin", MAX_STDIN_BYTES)
    expected = _bounded_text(test["expected_stdout"], "expected_stdout", MAX_EXPECTED_BYTES)
    return name, stdin, expected


def _load_request() -> tuple[str, tuple[tuple[str, str, str], ...], float]:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ProtocolError("request_too_large")
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError("invalid_json") from error
    request = _mapping(parsed, "request")
    if frozenset(request) != REQUEST_KEYS:
        raise ProtocolError("request_fields_invalid")
    if request["schema_version"] != 1 or type(request["schema_version"]) is not int:
        raise ProtocolError("schema_version_invalid")
    source = _bounded_text(request["source"], "source", MAX_SOURCE_BYTES)
    timeout = request["per_test_timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ProtocolError("timeout_invalid")
    timeout_value = float(timeout)
    if not 0 < timeout_value <= MAX_TIMEOUT_SECONDS:
        raise ProtocolError("timeout_invalid")
    raw_tests = request["tests"]
    if not isinstance(raw_tests, list) or not 1 <= len(raw_tests) <= MAX_TESTS:
        raise ProtocolError("tests_invalid")
    seen_names: set[str] = set()
    tests = [_load_test(raw_test, index, seen_names) for index, raw_test in enumerate(raw_tests)]
    return source, tuple(tests), timeout_value


def _limit_candidate() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_PROCESS_OUTPUT_BYTES, MAX_PROCESS_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    resource.setrlimit(resource.RLIMIT_NPROC, (16, 16))


def _write_candidate(source: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix="candidate-", suffix=".py", dir="/tmp")
    path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(source)
        path.chmod(0o400)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _run_test(
    candidate: Path, name: str, stdin: str, expected: str, timeout: float
) -> dict[str, object]:
    environment = {
        "HOME": "/tmp",  # noqa: S108 - isolated container tmpfs is the intended home.
        "LANG": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }
    with (
        tempfile.TemporaryFile(dir="/tmp") as stdout_file,
        tempfile.TemporaryFile(dir="/tmp") as stderr_file,
    ):
        try:
            completed = subprocess.run(  # noqa: S603 - candidate runs only in the sandbox.
                [sys.executable, "-I", "-B", str(candidate)],
                input=stdin.encode("utf-8"),
                stdout=stdout_file,
                stderr=stderr_file,
                cwd="/tmp",  # noqa: S108 - isolated container tmpfs is the intended cwd.
                env=environment,
                check=False,
                timeout=timeout,
                preexec_fn=_limit_candidate,
            )
            timed_out = False
            exit_code: int | None = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = None
        stdout_file.seek(0)
        actual_bytes = stdout_file.read(MAX_PROCESS_OUTPUT_BYTES + 1)
        stderr_file.seek(0, os.SEEK_END)
        stderr_bytes = stderr_file.tell()
    output_overflow = len(actual_bytes) > MAX_PROCESS_OUTPUT_BYTES
    try:
        actual = actual_bytes.decode("utf-8")
    except UnicodeDecodeError:
        actual = ""
        output_overflow = True
    passed = (
        not timed_out
        and exit_code == 0
        and not output_overflow
        and stderr_bytes == 0
        and actual == expected
    )
    return {
        "name": name,
        "passed": passed,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "output_overflow": output_overflow,
    }


def main() -> int:
    try:
        source, tests, timeout = _load_request()
        candidate = _write_candidate(source)
    except ProtocolError as error:
        _fail(str(error))
    except OSError:
        _fail("internal_io_error")
    try:
        results = [
            _run_test(candidate, name, stdin, expected, timeout) for name, stdin, expected in tests
        ]
    finally:
        candidate.unlink(missing_ok=True)
    passed_count = sum(result["passed"] is True for result in results)
    all_passed = passed_count == len(results)
    json.dump(
        {
            "schema_version": 1,
            "passed": all_passed,
            "summary": {"passed": passed_count, "total": len(results)},
            "tests": results,
        },
        sys.stdout,
        sort_keys=True,
        separators=(",", ":"),
    )
    sys.stdout.write("\n")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
