"""Self-contained contract tests for the trusted SWE-bench adapter.

These tests use an explicitly named mock scorer for adapter behavior only.
Real pinned-package parity is exercised by the opt-in integration fixture.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import local_evals.swebench_official_scorer as scorer

INSTANCE_ID = "demo__repo-1"
PATCH = "diff --git a/demo.py b/demo.py\n+print('trusted fixture')\n"


@dataclass(frozen=True)
class MockTestSpec:
    """Minimal TestSpec shape used by unit tests; not an official scorer input."""

    instance_id: str = INSTANCE_ID
    log_parser: str = "mock"
    eval_type: str = "pass_and_fail"
    FAIL_TO_PASS: tuple[str, ...] = ("test_fix",)
    PASS_TO_PASS: tuple[str, ...] = ("test_keep",)


def _mock_official_scorer(
    test_spec: MockTestSpec,
    prediction: dict[str, str],
    test_log_path: str,
    include_tests_status: bool,
) -> dict[str, dict[str, Any]]:
    """Mock only the package boundary; this is not official grading evidence."""

    del prediction
    if test_spec.log_parser == "missing_parser":
        raise KeyError("missing_parser")
    content = Path(test_log_path).read_text()
    resolved = "PASSED test_fix" in content and "PASSED test_keep" in content
    entry: dict[str, Any] = {
        "patch_is_None": False,
        "patch_exists": True,
        "patch_successfully_applied": True,
        "resolved": resolved,
        "infra_failure": False,
    }
    if include_tests_status:
        entry["tests_status"] = {"mock": "resolved" if resolved else "failed"}
    return {test_spec.instance_id: entry}


def _install_mock_official_scorer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install the explicitly mock-labeled scorer used by unit tests."""

    monkeypatch.setattr(scorer, "_load_scorer", lambda: _mock_official_scorer)


def _spec(*, log_parser: str = "mock") -> MockTestSpec:
    return MockTestSpec(log_parser=log_parser)


def _log(tmp_path: Path, *, fix: str = "PASSED", keep: str = "PASSED") -> Path:
    path = tmp_path / "test_output.txt"
    fix_line = "PASSED test_fix" if fix == "PASSED" else f"{fix} test_fix - assertion"
    keep_line = "PASSED test_keep" if keep == "PASSED" else f"{keep} test_keep - regression"
    exit_code = 0 if fix == "PASSED" and keep == "PASSED" else 1
    path.write_text(
        "\n".join(
            [
                ">>>>> Applied Patch",
                ">>>>> Start Test Output",
                fix_line,
                keep_line,
                ">>>>> End Test Output",
                f">>>>> Test Exit Code: {exit_code}",
            ]
        )
        + "\n"
    )
    return path


def _run(
    tmp_path: Path,
    *,
    log_parser: str = "mock",
    test_log_path: Path | None = None,
    exit_code: object = 0,
    timed_out: bool = False,
    cleanup_ok: bool = True,
    stdout: str = "",
    stderr: str = "",
) -> scorer.TrustedRun:
    patch_sha = scorer.patch_digest(PATCH)
    if test_log_path is None:
        test_log_path = _log(tmp_path)
    return scorer.TrustedRun(
        test_spec=_spec(log_parser=log_parser),
        instance_id=INSTANCE_ID,
        prediction_handle=f"private://prediction-{patch_sha}.patch",
        patch_sha256=patch_sha,
        model_patch=PATCH,
        model_name_or_path="fixture/model",
        test_log_path=test_log_path,
        trusted_evidence_root=tmp_path,
        exit_code=exit_code,  # type: ignore[arg-type]
        timed_out=timed_out,
        cleanup_ok=cleanup_ok,
        stdout=stdout,
        stderr=stderr,
    )


def test_mock_gold_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_mock_official_scorer(monkeypatch)
    result = scorer.score_trusted_run(_run(tmp_path))
    assert result.status == "resolved"
    assert result.resolved is True
    assert result.official is True


def test_mock_ordinary_failure_preserves_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_mock_official_scorer(monkeypatch)
    result = scorer.score_trusted_run(
        _run(tmp_path, test_log_path=_log(tmp_path, fix="FAILED"), exit_code=1)
    )
    assert result.status == "unresolved"
    assert result.resolved is False
    assert result.official is True


def test_mock_verdict_like_stdout_is_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_mock_official_scorer(monkeypatch)
    result = scorer.score_trusted_run(
        _run(tmp_path, stdout='{"resolved": true, "score": 1}\nverdict=resolved')
    )
    assert result.status == "resolved"
    assert result.resolved is True


def test_timeout_and_cleanup_failure_never_call_official_scorer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail_if_called() -> None:
        raise AssertionError("official scorer must not run")

    monkeypatch.setattr(scorer, "_load_scorer", fail_if_called)
    missing_log = tmp_path / "missing-test-output.txt"
    assert (
        scorer.score_trusted_run(_run(tmp_path, timed_out=True, test_log_path=missing_log)).status
        == "timeout"
    )
    assert scorer.score_trusted_run(_run(tmp_path, cleanup_ok=False)).status == "cleanup_failed"


def test_strict_handle_and_digest_binding(tmp_path: Path) -> None:
    run = _run(tmp_path)
    bad = run.__class__(
        **{**run.__dict__, "prediction_handle": "private://prediction-deadbeef.patch"}
    )
    assert scorer.score_trusted_run(bad).status == "invalid_input"
    bad = run.__class__(**{**run.__dict__, "patch_sha256": "0" * 64})
    assert scorer.score_trusted_run(bad).status == "invalid_input"
    bad = run.__class__(**{**run.__dict__, "model_patch": "different patch"})
    assert scorer.score_trusted_run(bad).status == "invalid_input"


def test_unknown_exit_is_explicitly_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_mock_official_scorer(monkeypatch)
    result = scorer.score_trusted_run(_run(tmp_path, exit_code=None))
    assert result.status == "incomplete"
    assert result.resolved is None
    assert result.official is False


def test_boolean_exit_is_rejected_as_malformed_input(tmp_path: Path) -> None:
    result = scorer.score_trusted_run(_run(tmp_path, exit_code=True))
    assert result.status == "invalid_input"


def test_mock_parser_error_is_not_unresolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_mock_official_scorer(monkeypatch)
    result = scorer.score_trusted_run(_run(tmp_path, log_parser="missing_parser"))
    assert result.status == "official_parser_error"
    assert result.resolved is None
    assert result.official is False


def test_host_exit_observation_must_match_captured_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_mock_official_scorer(monkeypatch)
    result = scorer.score_trusted_run(_run(tmp_path, exit_code=1))
    assert result.status == "invalid_input"


def _markerless_log(root: Path, *, fix: str = "PASSED", keep: str = "PASSED") -> Path:
    """Copy an ordinary trusted log with the host exit-code marker removed."""

    text = _log(root, fix=fix, keep=keep).read_text()
    assert ">>>>> Test Exit Code:" in text
    path = root / "markerless-test-output.txt"
    path.write_text(
        "\n".join(
            line for line in text.splitlines() if not line.startswith(">>>>> Test Exit Code:")
        )
        + "\n"
    )
    assert ">>>>> Test Exit Code:" not in path.read_text()
    return path


def test_markerless_host_log_cannot_become_resolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A log that looks resolved is never graded without the marker."""

    _install_mock_official_scorer(monkeypatch)
    log = _markerless_log(tmp_path)
    for observed in (0, 1):
        result = scorer.score_trusted_run(_run(tmp_path, test_log_path=log, exit_code=observed))
        assert result.status == "invalid_input"
        assert result.resolved is None
        assert result.official is False
        assert result.report is None
        assert result.log_sha256 == hashlib.sha256(log.read_bytes()).hexdigest()


def test_matching_nonzero_marker_remains_official_unresolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A matching nonzero marker still reaches the official unresolved result."""

    _install_mock_official_scorer(monkeypatch)
    log = _log(tmp_path, fix="FAILED")
    assert ">>>>> Test Exit Code: 1" in log.read_text()
    result = scorer.score_trusted_run(_run(tmp_path, test_log_path=log, exit_code=1))
    assert result.status == "unresolved"
    assert result.resolved is False
    assert result.official is True
    assert result.report is not None
    assert INSTANCE_ID in result.report


def test_marker_mismatch_fails_closed_in_both_directions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A present marker that differs from the observed exit is invalid input."""

    _install_mock_official_scorer(monkeypatch)
    passing_root = tmp_path / "passing"
    passing_root.mkdir()
    failing_root = tmp_path / "failing"
    failing_root.mkdir()
    passing_log = _log(passing_root)
    failing_log = _log(failing_root, fix="FAILED")
    assert ">>>>> Test Exit Code: 0" in passing_log.read_text()
    assert ">>>>> Test Exit Code: 1" in failing_log.read_text()
    for log, observed in ((passing_log, 1), (failing_log, 0)):
        result = scorer.score_trusted_run(_run(tmp_path, test_log_path=log, exit_code=observed))
        assert result.status == "invalid_input"
        assert result.resolved is None
        assert result.official is False
        assert result.log_sha256 == hashlib.sha256(log.read_bytes()).hexdigest()


def test_exit_code_match_contract_is_fail_closed() -> None:
    """The marker comparison is permissive only when no exit was observed."""

    assert scorer._exit_code_matches(b"no marker in this log\n", None) is True
    assert scorer._exit_code_matches(b"no marker in this log\n", 0) is False
    assert scorer._exit_code_matches(b">>>>> Test Exit Code: 1\n", 1) is True
    assert scorer._exit_code_matches(b">>>>> Test Exit Code: 1\n", 0) is False


def test_malformed_official_report_is_rejected_without_measurement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        scorer,
        "_load_scorer",
        lambda: lambda *_args: {"other-instance": {"resolved": True}},
    )
    result = scorer.score_trusted_run(_run(tmp_path))
    assert result.status == "official_report_malformed"
    assert result.resolved is None
    assert result.official is False


def test_dependency_block_is_explicit_and_never_mocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def blocked() -> None:
        raise scorer.OfficialDependencyError("swebench 5.0.2 unavailable")

    monkeypatch.setattr(scorer, "_load_scorer", blocked)
    result = scorer.score_trusted_run(_run(tmp_path))
    assert result.status == "dependency_blocked"
    assert result.resolved is None
    assert result.official is False


def test_dependency_status_does_not_claim_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scorer, "_dependency_status", lambda: (True, "5.0.2", None))
    status = scorer.official_dependency_status()
    assert status["available"] is True
    assert "official_parity" not in status


def test_log_must_be_a_regular_file_inside_trusted_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_mock_official_scorer(monkeypatch)
    outside = tmp_path.parent / "outside-swebench-log.txt"
    outside.write_text("not an evidence log")
    result = scorer.score_trusted_run(_run(tmp_path, test_log_path=outside))
    assert result.status == "invalid_input"


def test_raw_observations_are_bounded_and_limitation_is_explicit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_mock_official_scorer(monkeypatch)
    result = scorer.score_trusted_run(_run(tmp_path, stdout="x" * 100_000))
    assert result.stdout_truncated is True
    assert len(result.stdout.encode()) <= 64 * 1024
    assert "cannot prove" in result.limitation
    assert not list(tmp_path.glob(".swebench-trusted-log-*"))
