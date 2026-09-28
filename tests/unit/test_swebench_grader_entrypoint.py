"""Contract tests for the isolated SWE-bench grader entrypoint."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest


def _load_entrypoint() -> ModuleType:
    module_path = Path(__file__).resolve().parents[2] / "sandbox/swebench/grader_entrypoint.py"
    spec = importlib.util.spec_from_file_location(
        "swebench_grader_entrypoint_under_test", module_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


entrypoint = _load_entrypoint()


def _request(
    patch: bytes = b"diff --git a/file b/file\n",
    script: bytes = b"#!/bin/bash\nset -uxo pipefail\nprintf 'fixture\\n'\n",
) -> dict[str, object]:
    return {
        "schema_version": entrypoint.SCHEMA_VERSION,
        "patch_b64": base64.b64encode(patch).decode("ascii"),
        "eval_script_b64": base64.b64encode(script).decode("ascii"),
        "expected_patch_sha256": hashlib.sha256(patch).hexdigest(),
        "expected_eval_script_sha256": hashlib.sha256(script).hexdigest(),
        "timeout_seconds": 30,
        "max_output_bytes": 4096,
    }


def _set_baseline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: bytes = b"") -> Path:
    baseline = tmp_path / "testbed-baseline.txt"
    baseline.write_bytes(content)
    baseline.chmod(0o444)
    monkeypatch.setattr(entrypoint, "TESTBED_BASELINE", baseline)
    monkeypatch.setattr(entrypoint, "BASELINE_OWNER_UID", os.getuid())
    return baseline


def _prepare_sandbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    scratch = tmp_path / "entrypoint-scratch"
    testbed = tmp_path / "testbed"
    testbed.mkdir()
    (testbed / ".git").mkdir()
    monkeypatch.setattr(entrypoint, "SCRATCH_ROOT", scratch)
    monkeypatch.setattr(entrypoint, "TESTBED", testbed)
    _set_baseline(monkeypatch, tmp_path)
    return testbed


def test_pinned_fallback_applies_once_and_stops_before_eval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    testbed = _prepare_sandbox(monkeypatch, tmp_path)
    calls: list[tuple[tuple[str, ...], Path, bool, tuple[int, ...]]] = []
    remaining_timeouts: list[tuple[tuple[str, ...], float]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        deadline: float,
        max_output_bytes: int,
        pass_fds: tuple[int, ...] = (),
        merge_stderr: bool = False,
    ) -> entrypoint.ProcessCapture:
        del max_output_bytes
        calls.append((args, cwd, merge_stderr, pass_fds))
        remaining_timeouts.append((args, deadline - time.monotonic()))
        if args[:2] == ("git", "status"):
            return entrypoint.ProcessCapture(b"", b"", 0, False, False)
        if args[0] == "git" and args[1] == "apply":
            return entrypoint.ProcessCapture(b"rejected\n", b"", 1, False, False)
        if args[:3] == ("git", "reset", "--hard"):
            return entrypoint.ProcessCapture(b"", b"", 0, False, False)
        if args[:2] == ("git", "clean"):
            return entrypoint.ProcessCapture(b"", b"", 0, False, False)
        if args[0] == "patch":
            staged_paths = sorted(
                path.name for path in (entrypoint.SCRATCH_ROOT / "input").iterdir()
            )
            assert staged_paths == [entrypoint.PATCH_FILENAME]
            return entrypoint.ProcessCapture(b"applied\n", b"", 0, False, False)
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(entrypoint, "_run_bounded", fake_run)
    request = entrypoint._parse_request(json.dumps(_request()).encode())

    result = entrypoint._run_request(request)

    patch_calls = [call for call in calls if call[0][:2] == ("git", "apply")]
    expected_patch_path = str(tmp_path / "entrypoint-scratch/input/candidate.patch")
    assert [call[0] for call in patch_calls] == [
        ("git", "apply", "--verbose", expected_patch_path),
        (
            "git",
            "apply",
            "--verbose",
            "--3way",
            expected_patch_path,
        ),
        ("git", "apply", "--verbose", "--reject", expected_patch_path),
    ]
    assert (
        "patch",
        "--batch",
        "--forward",
        "--fuzz=5",
        "-p1",
        "-i",
        expected_patch_path,
    ) in [call[0] for call in calls]
    assert sum(call[0][:3] == ("git", "reset", "--hard") for call in calls) == 3
    assert all(call[1] == testbed for call in calls)
    assert not any(call[0][0] == "/bin/bash" for call in calls)
    patch_timeout = next(remaining for args, remaining in remaining_timeouts if args[0] == "patch")
    assert 0 < patch_timeout <= 30
    assert result["process_exit_code"] == 0
    assert result["phase"] == entrypoint.PHASE_PATCH_READY
    assert result["eval_exit_code"] is None
    assert result["error"] is None
    assert base64.b64decode(result["stdout_b64"]) == b"applied\n"
    assert base64.b64decode(result["stderr_b64"]) == b""
    assert not (tmp_path / "entrypoint-scratch").exists()


def test_successful_apply_never_launches_eval_even_when_deadline_is_spent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_sandbox(monkeypatch, tmp_path)
    attempted: list[tuple[str, ...]] = []
    spawned: list[tuple[object, ...]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        deadline: float,
        max_output_bytes: int,
        pass_fds: tuple[int, ...] = (),
        merge_stderr: bool = False,
    ) -> entrypoint.ProcessCapture:
        del cwd, deadline, max_output_bytes, pass_fds, merge_stderr
        attempted.append(args)
        if args[:2] == ("git", "status"):
            return entrypoint.ProcessCapture(b"", b"", 0, False, False)
        if args[0] == "git" and args[1] == "apply":
            time.sleep(1.1)
            return entrypoint.ProcessCapture(b"applied", b"", 0, False, False)
        raise AssertionError(f"unexpected command: {args}")

    def record_spawn(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        spawned.append(args)
        raise OSError("expired deadline must not spawn")

    monkeypatch.setattr(entrypoint, "_run_bounded", fake_run)
    monkeypatch.setattr(entrypoint.subprocess, "Popen", record_spawn)
    request_data = _request()
    request_data["timeout_seconds"] = 1
    request = entrypoint._parse_request(json.dumps(request_data).encode())

    result = entrypoint._run_request(request)

    assert any(args[:2] == ("git", "apply") for args in attempted)
    assert not any(args[0] == "/bin/bash" for args in attempted)
    assert spawned == []
    assert result["error"] is None
    assert result["phase"] == entrypoint.PHASE_PATCH_READY
    assert result["eval_exit_code"] is None
    assert set(result) == {
        "schema_version",
        "phase",
        "process_exit_code",
        "eval_exit_code",
        "timed_out",
        "patch_sha256",
        "eval_script_sha256",
        "stdout_b64",
        "stderr_b64",
        "error",
    }
    assert not {"score", "verdict", "tests_passed"} & result.keys()


def test_failed_apply_resets_before_fallback_and_never_runs_eval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_sandbox(monkeypatch, tmp_path)
    calls: list[tuple[str, ...]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        deadline: float,
        max_output_bytes: int,
        pass_fds: tuple[int, ...] = (),
        merge_stderr: bool = False,
    ) -> entrypoint.ProcessCapture:
        del cwd, deadline, max_output_bytes, pass_fds, merge_stderr
        calls.append(args)
        if args[:2] == ("git", "status"):
            return entrypoint.ProcessCapture(b"", b"", 0, False, False)
        if args[0] == "git" and args[1] == "apply":
            return entrypoint.ProcessCapture(b"partial\n", b"", 1, False, False)
        if args[:3] == ("git", "reset", "--hard") or args[:2] == ("git", "clean"):
            return entrypoint.ProcessCapture(b"", b"", 0, False, False)
        if args[:3] == ("patch", "--batch", "--forward"):
            return entrypoint.ProcessCapture(b"patch failed\n", b"", 1, False, False)
        if args[:4] == ("git", "apply", "--check", "--reverse"):
            return entrypoint.ProcessCapture(b"", b"", 1, False, False)
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(entrypoint, "_run_bounded", fake_run)
    request = entrypoint._parse_request(json.dumps(_request()).encode())

    result = entrypoint._run_request(request)

    apply_calls = [args for args in calls if args[:2] == ("git", "apply")]
    assert len(apply_calls) == 4  # Three git modes plus the official reverse check.
    assert sum(args[:3] == ("git", "reset", "--hard") for args in calls) == 4
    assert sum(args[:2] == ("git", "clean") for args in calls) == 4
    patch_path = str(tmp_path / "entrypoint-scratch/input/candidate.patch")
    reverse_index = calls.index(("git", "apply", "--check", "--reverse", patch_path))
    last_reset_index = max(
        index for index, args in enumerate(calls) if args[:3] == ("git", "reset", "--hard")
    )
    last_clean_index = max(
        index for index, args in enumerate(calls) if args[:2] == ("git", "clean")
    )
    assert last_reset_index < reverse_index
    assert last_clean_index < reverse_index
    assert not any(args[0] == "/bin/bash" for args in calls)
    assert result["process_exit_code"] == entrypoint.EXIT_PATCH_FAILED
    assert result["eval_exit_code"] is None
    assert result["error"] == "patch_failed"
    assert not (tmp_path / "entrypoint-scratch").exists()


@pytest.mark.parametrize(
    ("reverse_capture", "expected_error", "expected_timed_out"),
    [
        (entrypoint.ProcessCapture(b"partial", b"", None, True, False), "timeout", True),
        (entrypoint.ProcessCapture(b"partial", b"", None, False, True), "output_limit", False),
    ],
)
def test_reverse_probe_timeout_or_output_limit_resets_testbed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reverse_capture: entrypoint.ProcessCapture,
    expected_error: str,
    expected_timed_out: bool,
) -> None:
    _prepare_sandbox(monkeypatch, tmp_path)
    calls: list[tuple[str, ...]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        deadline: float,
        max_output_bytes: int,
        pass_fds: tuple[int, ...] = (),
        merge_stderr: bool = False,
    ) -> entrypoint.ProcessCapture:
        del cwd, deadline, max_output_bytes, pass_fds, merge_stderr
        calls.append(args)
        if args[:2] == ("git", "status"):
            return entrypoint.ProcessCapture(b"", b"", 0, False, False)
        if args[:2] == ("git", "apply") and args[2:4] != ("--check", "--reverse"):
            return entrypoint.ProcessCapture(b"partial\n", b"", 1, False, False)
        if args[0] == "patch":
            return entrypoint.ProcessCapture(b"partial\n", b"", 1, False, False)
        if args[:4] == ("git", "apply", "--check", "--reverse"):
            return reverse_capture
        if args[:3] == ("git", "reset", "--hard") or args[:2] == ("git", "clean"):
            return entrypoint.ProcessCapture(b"", b"", 0, False, False)
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(entrypoint, "_run_bounded", fake_run)
    request = entrypoint._parse_request(json.dumps(_request()).encode())

    result = entrypoint._run_request(request)

    reverse_index = next(
        index
        for index, args in enumerate(calls)
        if args[:4] == ("git", "apply", "--check", "--reverse")
    )
    assert any(args[:3] == ("git", "reset", "--hard") for args in calls[reverse_index + 1 :])
    assert any(args[:2] == ("git", "clean") for args in calls[reverse_index + 1 :])
    assert result["error"] == expected_error
    assert result["timed_out"] is expected_timed_out
    assert result["eval_exit_code"] is None
    assert not any(args[0] == "/bin/bash" for args in calls)


def test_dirty_or_non_git_testbed_fails_before_staging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_sandbox(monkeypatch, tmp_path)
    calls: list[tuple[str, ...]] = []

    def fake_run(args: tuple[str, ...], **kwargs: object) -> entrypoint.ProcessCapture:
        del kwargs
        calls.append(args)
        return entrypoint.ProcessCapture(b" M baseline.py\n", b"", 0, False, False)

    monkeypatch.setattr(entrypoint, "_run_bounded", fake_run)
    request = entrypoint._parse_request(json.dumps(_request()).encode())

    result = entrypoint._run_request(request)

    assert calls == [("git", "status", "--porcelain", "--untracked-files=all")]
    assert result["error"] == "testbed_invalid"
    assert result["eval_exit_code"] is None
    assert not (tmp_path / "entrypoint-scratch").exists()


def test_preexisting_scratch_symlink_is_not_followed_or_cleaned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    testbed = _prepare_sandbox(monkeypatch, tmp_path)
    del testbed
    target = tmp_path / "attacker-controlled"
    target.mkdir()
    sentinel = target / "candidate.patch"
    sentinel.write_bytes(b"keep")
    scratch = tmp_path / "entrypoint-scratch"
    scratch.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(entrypoint, "SCRATCH_ROOT", scratch)
    monkeypatch.setattr(
        entrypoint,
        "_run_bounded",
        lambda *args, **kwargs: entrypoint.ProcessCapture(b"", b"", 0, False, False),
    )
    request = entrypoint._parse_request(json.dumps(_request()).encode())

    result = entrypoint._run_request(request)

    assert result["error"] == "unsafe_input"
    assert sentinel.read_bytes() == b"keep"


def test_parse_rejects_duplicate_keys_and_digest_mismatch() -> None:
    duplicate = b'{"schema_version":1,"schema_version":1}'
    with pytest.raises(entrypoint.EntryFailure, match="invalid_input"):
        entrypoint._parse_request(duplicate)

    request = _request()
    request["expected_patch_sha256"] = "0" * 64
    with pytest.raises(entrypoint.EntryFailure, match="digest_mismatch"):
        entrypoint._parse_request(json.dumps(request).encode())


def test_reset_failure_stops_fallbacks_and_never_runs_eval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_sandbox(monkeypatch, tmp_path)
    calls: list[tuple[str, ...]] = []

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        deadline: float,
        max_output_bytes: int,
        pass_fds: tuple[int, ...] = (),
        merge_stderr: bool = False,
    ) -> entrypoint.ProcessCapture:
        del cwd, deadline, max_output_bytes, pass_fds, merge_stderr
        calls.append(args)
        if args[:2] == ("git", "status"):
            return entrypoint.ProcessCapture(b"", b"", 0, False, False)
        if args[0] == "git" and args[1] == "apply":
            return entrypoint.ProcessCapture(b"partial\n", b"", 1, False, False)
        if args[:3] == ("git", "reset", "--hard"):
            return entrypoint.ProcessCapture(b"", b"reset rejected", 1, False, False)
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(entrypoint, "_run_bounded", fake_run)
    request = entrypoint._parse_request(json.dumps(_request()).encode())

    result = entrypoint._run_request(request)

    assert result["process_exit_code"] == entrypoint.EXIT_RESET_FAILED
    assert result["error"] == "reset_failed"
    assert result["eval_exit_code"] is None
    assert sum(args[:2] == ("git", "apply") for args in calls) == 1
    assert not any(args[0] in {"patch", "/bin/bash"} for args in calls)
    assert not (tmp_path / "entrypoint-scratch").exists()


def test_three_way_conflict_reset_clears_index_before_next_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    testbed = tmp_path / "testbed"
    testbed.mkdir()

    def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(  # noqa: S603 - fixed synthetic repository commands.
            ("git", *args),  # noqa: S607 - fixed synthetic repository command.
            cwd=testbed,
            check=check,
            capture_output=True,
        )

    git("init", "--quiet")
    git("config", "user.name", "Synthetic Fixture")
    git("config", "user.email", "fixture@example.com")
    git("config", "commit.gpgsign", "false")
    tracked = testbed / "conflict.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git("add", "conflict.txt")
    git("commit", "--quiet", "-m", "base")
    tracked.write_text("patch side\n", encoding="utf-8")
    patch_bytes = git("diff", "--binary", "--", "conflict.txt").stdout
    tracked.write_text("HEAD side\n", encoding="utf-8")
    git("add", "conflict.txt")
    git("commit", "--quiet", "-m", "head side")
    patch_path = tmp_path / "candidate.patch"
    patch_path.write_bytes(patch_bytes)
    monkeypatch.setattr(entrypoint, "TESTBED", testbed)
    _set_baseline(monkeypatch, tmp_path)

    calls: list[tuple[str, ...]] = []
    original_run = entrypoint._run_bounded

    def fake_then_real_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        deadline: float,
        max_output_bytes: int,
        pass_fds: tuple[int, ...] = (),
        merge_stderr: bool = False,
    ) -> entrypoint.ProcessCapture:
        calls.append(args)
        if args[:2] == ("git", "status"):
            return original_run(
                args,
                cwd=cwd,
                deadline=deadline,
                max_output_bytes=max_output_bytes,
                pass_fds=pass_fds,
                merge_stderr=merge_stderr,
            )
        if args == ("git", "apply", "--verbose", str(patch_path)):
            return entrypoint.ProcessCapture(b"first mode rejected", b"", 1, False, False)
        if args == ("git", "apply", "--verbose", "--3way", str(patch_path)):
            return original_run(
                args,
                cwd=cwd,
                deadline=deadline,
                max_output_bytes=max_output_bytes,
                pass_fds=pass_fds,
                merge_stderr=merge_stderr,
            )
        if args == ("git", "apply", "--verbose", "--reject", str(patch_path)):
            assert git("status", "--porcelain").stdout == b""
            assert git("ls-files", "--unmerged").stdout == b""
            return entrypoint.ProcessCapture(b"fallback applied", b"", 0, False, False)
        return original_run(
            args,
            cwd=cwd,
            deadline=deadline,
            max_output_bytes=max_output_bytes,
            pass_fds=pass_fds,
            merge_stderr=merge_stderr,
        )

    monkeypatch.setattr(entrypoint, "_run_bounded", fake_then_real_run)

    result = entrypoint._apply_official_patch(
        patch_path,
        deadline=time.monotonic() + 10,
        max_output_bytes=4096,
        patch_sha256="a" * 64,
        eval_script_sha256="b" * 64,
    )

    assert result.exit_code == 0
    assert any("--3way" in args for args in calls)
    assert calls.index(("git", "apply", "--verbose", "--3way", str(patch_path))) < calls.index(
        ("git", "apply", "--verbose", "--reject", str(patch_path))
    )
    assert git("status", "--porcelain").stdout == b""


def test_main_returns_one_bounded_machine_record_for_bad_schema() -> None:
    stdout = io.BytesIO()

    exit_code = entrypoint.main(io.BytesIO(b"{}"), stdout)

    assert exit_code == entrypoint.EXIT_INVALID_INPUT
    record = json.loads(stdout.getvalue())
    assert record == entrypoint._result(
        process_exit_code=entrypoint.EXIT_INVALID_INPUT,
        error="invalid_input",
    )


def test_bounded_child_capture_stops_at_output_limit(tmp_path: Path) -> None:
    capture = entrypoint._run_bounded(
        (
            sys.executable,
            "-c",
            "import os; os.write(1, b'x' * 200000)",
        ),
        cwd=tmp_path,
        deadline=time.monotonic() + 5,
        max_output_bytes=128,
    )

    assert capture.output_exceeded is True
    assert capture.timed_out is False
    assert len(capture.stdout) + len(capture.stderr) == 128
    assert capture.exit_code is None


def test_bounded_child_capture_times_out_and_reaps(tmp_path: Path) -> None:
    capture = entrypoint._run_bounded(
        (sys.executable, "-c", "import time; time.sleep(10)"),
        cwd=tmp_path,
        deadline=time.monotonic() + 0.05,
        max_output_bytes=128,
    )

    assert capture.timed_out is True
    assert capture.output_exceeded is False
    assert capture.exit_code is None


def test_bounded_child_merges_stderr_when_requested(tmp_path: Path) -> None:
    capture = entrypoint._run_bounded(
        (
            sys.executable,
            "-c",
            "import os; os.write(1, b'out'); os.write(2, b'err')",
        ),
        cwd=tmp_path,
        deadline=time.monotonic() + 5,
        max_output_bytes=128,
        merge_stderr=True,
    )

    assert capture.exit_code == 0
    assert capture.stdout == b"outerr"
    assert capture.stderr == b""


def test_eval_script_bytes_are_verified_but_never_staged_or_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_sandbox(monkeypatch, tmp_path)
    calls: list[tuple[str, ...]] = []
    script = b"this would fail if the grader entrypoint tried to execute it\n"
    request = entrypoint._parse_request(json.dumps(_request(script=script)).encode())

    def fake_run(
        args: tuple[str, ...],
        *,
        cwd: Path,
        deadline: float,
        max_output_bytes: int,
        pass_fds: tuple[int, ...] = (),
        merge_stderr: bool = False,
    ) -> entrypoint.ProcessCapture:
        del cwd, deadline, max_output_bytes, pass_fds, merge_stderr
        calls.append(args)
        if args[:2] == ("git", "status"):
            return entrypoint.ProcessCapture(b"", b"", 0, False, False)
        if args[:2] == ("git", "apply"):
            staged_paths = sorted(
                path.name for path in (entrypoint.SCRATCH_ROOT / "input").iterdir()
            )
            assert staged_paths == [entrypoint.PATCH_FILENAME]
            return entrypoint.ProcessCapture(b"patch ready\n", b"", 0, False, False)
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(entrypoint, "_run_bounded", fake_run)
    result = entrypoint._run_request(request)

    assert result["phase"] == entrypoint.PHASE_PATCH_READY
    assert result["eval_exit_code"] is None
    assert result["eval_script_sha256"] == hashlib.sha256(script).hexdigest()
    assert not any(args[0] == "/bin/bash" for args in calls)


def _git_testbed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    testbed = tmp_path / "testbed"
    testbed.mkdir()
    for args in (
        ("init", "--quiet"),
        ("config", "user.name", "Synthetic Fixture"),
        ("config", "user.email", "fixture@example.com"),
        ("config", "commit.gpgsign", "false"),
    ):
        subprocess.run(("git", *args), cwd=testbed, check=True, capture_output=True)  # noqa: S603,S607
    (testbed / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(("git", "add", "tracked.txt"), cwd=testbed, check=True)  # noqa: S603,S607
    subprocess.run(  # noqa: S603 - fixed synthetic repository command.
        ("git", "commit", "--quiet", "-m", "base"),  # noqa: S607
        cwd=testbed,
        check=True,
    )
    monkeypatch.setattr(entrypoint, "TESTBED", testbed)
    return testbed


def test_pristine_untracked_files_match_the_build_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    testbed = _git_testbed(monkeypatch, tmp_path)
    (testbed / "build").mkdir()
    (testbed / "build" / "lib.py").write_text("shipped by the image\n", encoding="utf-8")
    _set_baseline(monkeypatch, tmp_path, b"?? build/lib.py\n")

    entrypoint._verify_fresh_testbed(time.monotonic() + 30)

    (testbed / "extra.py").write_text("not in the image\n", encoding="utf-8")
    with pytest.raises(entrypoint.EntryFailure, match="testbed_invalid"):
        entrypoint._verify_fresh_testbed(time.monotonic() + 30)


def test_after_clean_only_tracked_baseline_entries_are_expected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    testbed = _git_testbed(monkeypatch, tmp_path)
    _set_baseline(monkeypatch, tmp_path, b"?? build/lib.py\n")

    # `git clean -fd` removed the image's untracked build/ tree.
    entrypoint._verify_fresh_testbed(time.monotonic() + 30, after_clean=True)
    with pytest.raises(entrypoint.EntryFailure, match="testbed_invalid"):
        entrypoint._verify_fresh_testbed(time.monotonic() + 30)

    (testbed / "tracked.txt").write_text("modified\n", encoding="utf-8")
    with pytest.raises(entrypoint.EntryFailure, match="testbed_invalid"):
        entrypoint._verify_fresh_testbed(time.monotonic() + 30, after_clean=True)


def test_writable_or_missing_baseline_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _git_testbed(monkeypatch, tmp_path)
    baseline = _set_baseline(monkeypatch, tmp_path)
    baseline.chmod(0o644)
    with pytest.raises(entrypoint.EntryFailure, match="testbed_invalid"):
        entrypoint._verify_fresh_testbed(time.monotonic() + 30)

    monkeypatch.setattr(entrypoint, "TESTBED_BASELINE", tmp_path / "missing.txt")
    with pytest.raises(entrypoint.EntryFailure, match="testbed_invalid"):
        entrypoint._verify_fresh_testbed(time.monotonic() + 30)
