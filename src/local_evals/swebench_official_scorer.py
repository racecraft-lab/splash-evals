"""Trusted-side adapter for the pinned SWE-bench 5.0.2 scorer.

This module does not execute a patch, start a container, or trust a container
verdict.  The caller supplies a host-captured test log and a trusted
``TestSpec``; the pinned package supplies the only score calculation.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

SWEBENCH_VERSION = "5.0.2"
SWEBENCH_SOURCE_REVISION = "490635b2e9e775dca1e1d6b40ce9dbcff91e780f"
SWEBENCH_WHEEL_SHA256 = (
    "b7f0416a1e686eca22c2f749b5f816685a202835032f6683080e2b53545bbb62",
    "faae7b1307b8188e842e78ef5826cf0d64fa78486adc41e3414f9533b9e0d7eb",
)

_INSTANCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\Z")
_HANDLE_RE = re.compile(r"private://prediction-([0-9a-f]{64})\.patch\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_EXIT_CODE_RE = re.compile(r"^>>>>> Test Exit Code:\s*(-?\d+)\s*$", re.MULTILINE)
_MAX_CAPTURE_BYTES = 64 * 1024
_MAX_LOG_BYTES = 4 * 1024 * 1024

PARSER_AUTHENTICITY_LIMITATION = (
    "SWE-bench parses the trusted host-captured log, but parser output cannot "
    "prove that a malicious patch did not forge test-like lines; this adapter "
    "does not claim malicious-code score authenticity."
)


class ScorerInputError(ValueError):
    """The trusted request or host observation is malformed."""


class OfficialDependencyError(RuntimeError):
    """The pinned official scorer is unavailable or incompatible."""


@dataclass(frozen=True)
class TrustedRun:
    """Host-owned inputs captured after the disposable grader exits."""

    test_spec: Any
    instance_id: str
    prediction_handle: str
    patch_sha256: str
    model_patch: str
    model_name_or_path: str
    test_log_path: Path
    trusted_evidence_root: Path
    exit_code: int | None
    timed_out: bool = False
    cleanup_ok: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class ScoreResult:
    status: str
    resolved: bool | None
    official: bool
    report: Mapping[str, Any] | None = None
    reason: str | None = None
    stdout: str = ""
    stdout_truncated: bool = False
    stderr: str = ""
    stderr_truncated: bool = False
    log_sha256: str | None = None
    limitation: str = PARSER_AUTHENTICITY_LIMITATION

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "resolved": self.resolved,
            "official": self.official,
            "report": dict(self.report) if self.report is not None else None,
            "reason": self.reason,
            "stdout": self.stdout,
            "stdout_truncated": self.stdout_truncated,
            "stderr": self.stderr,
            "stderr_truncated": self.stderr_truncated,
            "log_sha256": self.log_sha256,
            "limitation": self.limitation,
        }


def _bounded(value: str) -> tuple[str, bool]:
    raw = value.encode("utf-8", errors="replace")
    if len(raw) <= _MAX_CAPTURE_BYTES:
        return value, False
    return raw[:_MAX_CAPTURE_BYTES].decode("utf-8", errors="replace"), True


def _failure(
    status: str,
    reason: str,
    run: TrustedRun,
    *,
    log_sha256: str | None = None,
) -> ScoreResult:
    stdout, stdout_truncated = _bounded(run.stdout)
    stderr, stderr_truncated = _bounded(run.stderr)
    return ScoreResult(
        status=status,
        resolved=None,
        official=False,
        reason=reason,
        stdout=stdout,
        stdout_truncated=stdout_truncated,
        stderr=stderr,
        stderr_truncated=stderr_truncated,
        log_sha256=log_sha256,
    )


def parse_prediction_handle(handle: str) -> str:
    """Return the patch digest from the one accepted opaque handle shape."""

    if not isinstance(handle, str):
        raise ScorerInputError("prediction handle must be a string")
    match = _HANDLE_RE.fullmatch(handle)
    if match is None:
        raise ScorerInputError(
            "prediction handle must match private://prediction-{64 lowercase hex}.patch"
        )
    return match.group(1)


def _bounded_log(path: Path, root: Path) -> tuple[bytes, str]:
    if root.is_symlink() or not root.is_dir():
        raise ScorerInputError("trusted evidence root must be a real directory")
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ScorerInputError("trusted test log must be a regular file")
    root_real = root.resolve(strict=True)
    path_real = path.resolve(strict=True)
    try:
        path_real.relative_to(root_real)
    except ValueError as exc:
        raise ScorerInputError("trusted test log is outside the evidence root") from exc
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path_real, flags)
    except OSError as exc:
        raise ScorerInputError("trusted test log could not be opened safely") from exc
    try:
        with os.fdopen(fd, "rb") as stream:
            data = stream.read(_MAX_LOG_BYTES + 1)
    except OSError as exc:
        raise ScorerInputError("trusted test log could not be read") from exc
    if len(data) > _MAX_LOG_BYTES:
        raise ScorerInputError("trusted test log exceeds the bounded size")
    return data, hashlib.sha256(data).hexdigest()


def _materialize_log(root: Path, data: bytes) -> Path:
    """Give the official scorer an immutable host-owned log snapshot."""

    with tempfile.NamedTemporaryFile(
        dir=root.resolve(strict=True),
        prefix=".swebench-trusted-log-",
        delete=False,
    ) as stream:
        stream.write(data)
        stream.flush()
        os.fchmod(stream.fileno(), 0o600)
        return Path(stream.name)


def _dependency_status() -> tuple[bool, str | None, str | None]:
    try:
        version = importlib.metadata.version("swebench")
    except importlib.metadata.PackageNotFoundError:
        return False, None, "swebench 5.0.2 is not installed"
    if version != SWEBENCH_VERSION:
        return (
            False,
            version,
            f"installed swebench version is {version}, expected 5.0.2",
        )
    try:
        grading = importlib.import_module("swebench.harness.grading")
        scorer = grading.get_eval_report
    except (ImportError, AttributeError) as exc:
        return False, version, f"pinned swebench get_eval_report is unavailable: {exc}"
    if not callable(scorer):
        return False, version, "pinned swebench get_eval_report is not callable"
    return True, version, None


def official_dependency_status() -> dict[str, Any]:
    """Report local compatibility without downloading or substituting a scorer."""

    available, version, reason = _dependency_status()
    return {
        "available": available,
        "installed_version": version,
        "expected_version": SWEBENCH_VERSION,
        "source_revision": SWEBENCH_SOURCE_REVISION,
        "wheel_sha256": list(SWEBENCH_WHEEL_SHA256),
        "reason": reason,
    }


def verify_pinned_wheel(wheel_path: Path) -> str:
    """Verify a local wheel against the hashes from the control-plane lock."""

    if wheel_path.is_symlink() or not wheel_path.is_file():
        raise OfficialDependencyError("SWE-bench wheel must be a regular file")
    digest = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    if digest not in SWEBENCH_WHEEL_SHA256:
        raise OfficialDependencyError("SWE-bench wheel hash is not pinned")
    return digest


def _load_scorer() -> Callable[[Any, dict[str, str], str, bool], Mapping[str, Any]]:
    available, _, reason = _dependency_status()
    if not available:
        raise OfficialDependencyError(reason or "pinned swebench scorer unavailable")
    grading = importlib.import_module("swebench.harness.grading")
    scorer = getattr(grading, "get_eval_report", None)
    if not callable(scorer):
        raise OfficialDependencyError("pinned get_eval_report is not callable")
    return cast(Callable[[Any, dict[str, str], str, bool], Mapping[str, Any]], scorer)


def _validate_request(run: TrustedRun) -> tuple[str, str]:
    if not _INSTANCE_RE.fullmatch(run.instance_id):
        raise ScorerInputError("instance_id is malformed")
    digest = parse_prediction_handle(run.prediction_handle)
    if not _SHA256_RE.fullmatch(run.patch_sha256):
        raise ScorerInputError("patch_sha256 must be lowercase hexadecimal")
    if digest != run.patch_sha256:
        raise ScorerInputError("prediction handle is not bound to patch_sha256")
    if not isinstance(run.model_patch, str) or not run.model_patch:
        raise ScorerInputError("trusted model patch must be present")
    if patch_digest(run.model_patch) != run.patch_sha256:
        raise ScorerInputError("trusted model patch does not match patch_sha256")
    if not isinstance(run.model_name_or_path, str) or not run.model_name_or_path:
        raise ScorerInputError("model_name_or_path must be present")
    if run.exit_code is not None and (
        isinstance(run.exit_code, bool) or not isinstance(run.exit_code, int)
    ):
        raise ScorerInputError("exit_code must be an integer or null")
    if not isinstance(run.timed_out, bool) or not isinstance(run.cleanup_ok, bool):
        raise ScorerInputError("timeout and cleanup observations must be boolean")
    if getattr(run.test_spec, "instance_id", None) != run.instance_id:
        raise ScorerInputError("TestSpec instance_id does not match the trusted run")
    return digest, run.instance_id


def _exit_code_matches(data: bytes, observed: int | None) -> bool:
    """Fail closed unless a captured marker corroborates the observed exit.

    An observed integer exit with no ``>>>>> Test Exit Code:`` marker in the
    trusted log cannot be corroborated, so it is treated as a disagreement
    instead of being trusted. ``observed is None`` means the host never saw
    the grader exit; that case is reported as ``incomplete`` before this
    check runs and stays permissive here.
    """

    if observed is None:
        return True
    matches = _EXIT_CODE_RE.findall(data.decode("utf-8", errors="replace"))
    if not matches:
        return False
    return int(matches[-1]) == observed


def _interpret_report(
    report: Mapping[str, Any], instance_id: str, run: TrustedRun, log_sha256: str
) -> ScoreResult:
    stdout, stdout_truncated = _bounded(run.stdout)
    stderr, stderr_truncated = _bounded(run.stderr)
    if set(report) != {instance_id} or not isinstance(report[instance_id], Mapping):
        return _failure(
            "official_report_malformed",
            "report keys or entry shape are invalid",
            run,
            log_sha256=log_sha256,
        )
    entry = report[instance_id]
    resolved = entry.get("resolved")
    infra_failure = entry.get("infra_failure")
    if not isinstance(resolved, bool) or not isinstance(infra_failure, bool):
        return _failure(
            "official_report_malformed",
            "report booleans are invalid",
            run,
            log_sha256=log_sha256,
        )
    bounded_report = {
        key: value
        for key, value in entry.items()
        if key
        in {
            "patch_is_None",
            "patch_exists",
            "patch_successfully_applied",
            "resolved",
            "infra_failure",
            "infra_failure_reason",
        }
    }
    if infra_failure:
        return ScoreResult(
            status="infrastructure_failure",
            resolved=None,
            official=True,
            report={instance_id: bounded_report},
            reason=str(
                entry.get(
                    "infra_failure_reason",
                    "official scorer reported infrastructure failure",
                )
            ),
            stdout=stdout,
            stdout_truncated=stdout_truncated,
            stderr=stderr,
            stderr_truncated=stderr_truncated,
            log_sha256=log_sha256,
        )
    return ScoreResult(
        status="resolved" if resolved else "unresolved",
        resolved=resolved,
        official=True,
        report={instance_id: bounded_report},
        stdout=stdout,
        stdout_truncated=stdout_truncated,
        stderr=stderr,
        stderr_truncated=stderr_truncated,
        log_sha256=log_sha256,
    )


def score_trusted_run(run: TrustedRun) -> ScoreResult:
    """Score a completed trusted run; never consume a container verdict."""

    try:
        _validate_request(run)
    except ScorerInputError as exc:
        return _failure("invalid_input", str(exc), run)
    if run.timed_out:
        return _failure("timeout", "trusted grader timed out", run)
    if not run.cleanup_ok:
        return _failure(
            "cleanup_failed",
            "trusted grader cleanup was not confirmed",
            run,
        )
    if run.exit_code is None:
        return _failure(
            "incomplete",
            "trusted host did not observe grader completion (exit_code is null)",
            run,
        )
    try:
        log_data, log_sha256 = _bounded_log(run.test_log_path, run.trusted_evidence_root)
    except ScorerInputError as exc:
        return _failure("invalid_input", str(exc), run)
    if not _exit_code_matches(log_data, run.exit_code):
        return _failure(
            "invalid_input",
            "host exit code disagrees with the captured SWE-bench marker",
            run,
            log_sha256=log_sha256,
        )
    # A nonzero exit is often the official signal for ordinary failing tests.
    # Let SWE-bench inspect its TEST_EXIT_CODE marker and status map; rejecting
    # every nonzero exit here would turn an ordinary unresolved case into an
    # infrastructure error and would change official semantics.
    scoring_log: Path | None = None
    try:
        scoring_log = _materialize_log(run.trusted_evidence_root, log_data)
        scorer = _load_scorer()
        report = scorer(
            run.test_spec,
            {
                "instance_id": run.instance_id,
                "model_name_or_path": run.model_name_or_path,
                "model_patch": run.model_patch,
            },
            str(scoring_log),
            True,
        )
    except OfficialDependencyError as exc:
        return _failure("dependency_blocked", str(exc), run, log_sha256=log_sha256)
    except Exception as exc:  # noqa: BLE001 - third-party parser; fail closed
        return _failure(
            "official_parser_error",
            f"official scorer raised {type(exc).__name__}: {exc}",
            run,
            log_sha256=log_sha256,
        )
    finally:
        if scoring_log is not None:
            scoring_log.unlink(missing_ok=True)
    if not isinstance(report, Mapping):
        return _failure(
            "official_report_malformed",
            "official scorer returned a non-mapping",
            run,
            log_sha256=log_sha256,
        )
    return _interpret_report(report, run.instance_id, run, log_sha256)


def patch_digest(patch: str) -> str:
    """Return the digest used to bind a trusted patch to its opaque handle."""

    if not isinstance(patch, str) or not patch:
        raise ScorerInputError("patch must be a non-empty string")
    return hashlib.sha256(patch.encode("utf-8")).hexdigest()
