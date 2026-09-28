"""Contract tests for the isolated SWE-bench 5.0.2 control RPC.

Injected scorer functions below are mocks for request validation and call
boundaries. They do not establish parity with official SWE-bench grading.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


def _load_entrypoint() -> ModuleType:
    module_path = Path(__file__).resolve().parents[2] / "sandbox/swebench/scorer_entrypoint.py"
    spec = importlib.util.spec_from_file_location(
        "swebench_scorer_entrypoint_under_test", module_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


entrypoint = _load_entrypoint()
INSTANCE_ID = "django__django-12345"
PATCH = "diff --git a/app.py b/app.py\n+inert patch text\n"
SOURCE_EVAL_SCRIPT = "#!/bin/bash\ntouch never-run\n"
CANONICAL_EVAL_SCRIPT = "#!/bin/bash\nset -uxo pipefail\ntouch never-run\n"
GRADING_LOG = (
    b">>>>> Applied Patch\n"
    b">>>>> Start Test Output\n"
    b"PASSED test_fix\n"
    b"PASSED test_keep\n"
    b">>>>> End Test Output\n"
)
RESPONSE_KEYS = {
    "schema_version",
    "action",
    "scorer_status",
    "error",
    "swebench_version",
    "instance_id",
    "test_spec_sha256",
    "eval_script_b64",
    "eval_script_sha256",
    "scorer_entrypoint_sha256",
    "patch_sha256",
    "grading_log_sha256",
    "host_cleanup_confirmed",
    "resolved",
    "report",
}


def _test_spec(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "instance_id": INSTANCE_ID,
        "image": "swebench/sweb.eval.x86_64.django_12345:latest",
        "repo": "django/django",
        "version": "verified",
        "FAIL_TO_PASS": ["test_fix"],
        "PASS_TO_PASS": ["test_keep"],
        "log_parser": "parse_log_pytest",
        "eval_type": "pass_and_fail",
        "eval_script": SOURCE_EVAL_SCRIPT,
        "image_assets": None,
    }
    value.update(overrides)
    return value


def _document(action: str = "prepare", **overrides: object) -> dict[str, object]:
    spec = _test_spec()
    value: dict[str, object] = {
        "schema_version": entrypoint.SCHEMA_VERSION,
        "action": action,
        "swebench_version": entrypoint.SWEBENCH_VERSION,
        "instance_id": INSTANCE_ID,
        "test_spec": spec,
    }
    if action == "score":
        patch_bytes = PATCH.encode("utf-8")
        value.update(
            test_spec_sha256=hashlib.sha256(entrypoint._canonical_test_spec(spec)).hexdigest(),
            eval_script_sha256=hashlib.sha256(CANONICAL_EVAL_SCRIPT.encode("utf-8")).hexdigest(),
            patch=PATCH,
            patch_sha256=hashlib.sha256(patch_bytes).hexdigest(),
            grading_log_b64=base64.b64encode(GRADING_LOG).decode("ascii"),
            grading_log_sha256=hashlib.sha256(GRADING_LOG).hexdigest(),
            host_cleanup_confirmed=True,
        )
    value.update(overrides)
    return value


def _encode(document: dict[str, object]) -> bytes:
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def _mock_functions(
    *, report: object | None = None
) -> tuple[Any, list[dict[str, object]], list[tuple[object, dict[str, str], bytes, bool]]]:
    make_calls: list[dict[str, object]] = []
    grade_calls: list[tuple[object, dict[str, str], bytes, bool]] = []

    def make_test_spec(instance: dict[str, object]) -> object:
        make_calls.append(instance)
        return SimpleNamespace(
            instance_id=instance["instance_id"],
            image=instance["image"],
            eval_script=CANONICAL_EVAL_SCRIPT,
        )

    def get_eval_report(
        test_spec: object,
        prediction: dict[str, str],
        test_log_path: str,
        include_tests_status: bool,
    ) -> object:
        grade_calls.append(
            (
                test_spec,
                prediction,
                Path(test_log_path).read_bytes(),
                include_tests_status,
            )
        )
        if report is not None:
            return report
        return {
            INSTANCE_ID: {
                "patch_is_None": False,
                "patch_exists": True,
                "patch_successfully_applied": True,
                "resolved": True,
                "infra_failure": False,
                "tests_status": {
                    "FAIL_TO_PASS": {"success": ["test_fix"], "failure": []},
                    "PASS_TO_PASS": {"success": ["test_keep"], "failure": []},
                },
            }
        }

    functions = entrypoint._ScorerFunctions(
        make_test_spec=make_test_spec,
        get_eval_report=get_eval_report,
        log_parser_names=frozenset({"parse_log_pytest"}),
    )
    return functions, make_calls, grade_calls


def test_prepare_returns_canonical_script_and_never_grades(
    tmp_path: Path,
) -> None:
    functions, make_calls, grade_calls = _mock_functions()
    sentinel = tmp_path / "never-run"
    request = _document(test_spec=_test_spec(eval_script=f"#!/bin/bash\ntouch {sentinel}\n"))

    result = entrypoint.score_payload(_encode(request), _test_functions=functions)

    assert result["scorer_status"] == "mock"
    assert result["swebench_version"] is None
    assert result["action"] == "prepare"
    assert result["instance_id"] == INSTANCE_ID
    assert (
        result["test_spec_sha256"]
        == hashlib.sha256(entrypoint._canonical_test_spec(request["test_spec"])).hexdigest()
    )
    assert result["eval_script_b64"] == base64.b64encode(
        CANONICAL_EVAL_SCRIPT.encode("utf-8")
    ).decode("ascii")
    assert (
        result["eval_script_sha256"]
        == hashlib.sha256(CANONICAL_EVAL_SCRIPT.encode("utf-8")).hexdigest()
    )
    assert len(result["scorer_entrypoint_sha256"]) == 64
    assert result["patch_sha256"] is None
    assert result["grading_log_sha256"] is None
    assert result["host_cleanup_confirmed"] is None
    assert result["resolved"] is None
    assert result["report"] is None
    assert set(result) == RESPONSE_KEYS
    assert len(make_calls) == 1
    assert make_calls[0] == request["test_spec"]
    assert grade_calls == []
    assert not sentinel.exists()


def test_score_calls_mock_scorer_once_with_frozen_identity_and_log() -> None:
    functions, make_calls, grade_calls = _mock_functions()
    request = _document("score")

    result = entrypoint.score_payload(_encode(request), _test_functions=functions)

    assert result["scorer_status"] == "mock"
    assert result["swebench_version"] is None
    assert result["action"] == "score"
    assert result["instance_id"] == INSTANCE_ID
    assert result["test_spec_sha256"] == request["test_spec_sha256"]
    assert result["eval_script_b64"] is None
    assert result["eval_script_sha256"] == request["eval_script_sha256"]
    assert result["patch_sha256"] == request["patch_sha256"]
    assert result["grading_log_sha256"] == request["grading_log_sha256"]
    assert result["host_cleanup_confirmed"] is True
    assert result["resolved"] is True
    assert set(result) == RESPONSE_KEYS
    assert len(make_calls) == 1
    assert len(grade_calls) == 1
    _test_spec_object, prediction, captured_log, include_tests_status = grade_calls[0]
    assert prediction == {
        "instance_id": INSTANCE_ID,
        "model_name_or_path": "local-evals/trusted-control-scorer",
        "model_patch": PATCH,
    }
    assert captured_log == GRADING_LOG
    assert include_tests_status is True


def test_host_cleanup_assertion_is_required_before_any_scorer_call() -> None:
    functions, make_calls, grade_calls = _mock_functions()
    request = _document("score", host_cleanup_confirmed=False)

    result = entrypoint.score_payload(_encode(request), _test_functions=functions)

    assert result["scorer_status"] == "invalid_input"
    assert result["error"] == "host_cleanup_not_confirmed"
    assert make_calls == []
    assert grade_calls == []


@pytest.mark.parametrize(
    "document",
    [
        {**_document(), "unexpected": "field"},
        {**_document(), "test_spec": {**_test_spec(), "unexpected": "field"}},
        {**_document(), "instance_id": "another__repo-1"},
        {**_document(), "swebench_version": "5.0.1"},
        {**_document(), "action": []},
    ],
)
def test_rejects_unknown_or_mismatched_schema_fields(
    document: dict[str, object],
) -> None:
    functions, make_calls, grade_calls = _mock_functions()

    result = entrypoint.score_payload(_encode(document), _test_functions=functions)

    assert result["scorer_status"] == "invalid_input"
    assert make_calls == []
    assert grade_calls == []


def test_rejects_duplicate_json_keys() -> None:
    functions, make_calls, grade_calls = _mock_functions()

    result = entrypoint.score_payload(
        b'{"schema_version":1,"schema_version":1}', _test_functions=functions
    )

    assert result["scorer_status"] == "invalid_input"
    assert result["error"] == "duplicate_json_key"
    assert make_calls == []
    assert grade_calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("test_spec_sha256", "0" * 64),
        ("eval_script_sha256", "0" * 64),
        ("patch_sha256", "0" * 64),
        ("grading_log_sha256", "0" * 64),
    ],
)
def test_rejects_identity_or_payload_digest_mismatch(field: str, value: str) -> None:
    functions, make_calls, grade_calls = _mock_functions()

    result = entrypoint.score_payload(
        _encode(_document("score", **{field: value})), _test_functions=functions
    )

    assert result["scorer_status"] == "invalid_input"
    assert len(make_calls) == (1 if field == "eval_script_sha256" else 0)
    assert grade_calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("log_parser", "untrusted.module.parse"),
        ("eval_type", "arbitrary_eval_type"),
    ],
)
def test_rejects_unregistered_parser_or_eval_type_before_scorer_calls(
    field: str, value: str
) -> None:
    functions, make_calls, grade_calls = _mock_functions()
    spec = _test_spec(**{field: value})

    result = entrypoint.score_payload(_encode(_document(test_spec=spec)), _test_functions=functions)

    assert result["scorer_status"] == "invalid_input"
    assert make_calls == []
    assert grade_calls == []


@pytest.mark.parametrize(
    "bad_report",
    [
        {"another__repo-1": {"resolved": True}},
        {INSTANCE_ID: {"resolved": "true"}},
        {INSTANCE_ID: {"resolved": True, "extra": object()}},
    ],
)
def test_malformed_official_report_is_never_returned_as_a_score(
    bad_report: object,
) -> None:
    functions, make_calls, grade_calls = _mock_functions(report=bad_report)

    result = entrypoint.score_payload(_encode(_document("score")), _test_functions=functions)

    assert result["scorer_status"] == "scorer_error"
    assert result["error"] == "official_scorer_failed"
    assert result["resolved"] is None
    assert result["report"] is None
    assert len(make_calls) == 1
    assert len(grade_calls) == 1


def test_missing_pinned_package_fails_closed_without_official_parity_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def package_missing(_name: str) -> str:
        raise entrypoint.importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(entrypoint.importlib.metadata, "version", package_missing)

    result = entrypoint.score_payload(_encode(_document("score")))

    assert result["scorer_status"] == "dependency_unavailable"
    assert result["error"] == "swebench_unavailable"
    assert result["swebench_version"] is None
    assert result["resolved"] is None
    assert result["report"] is None


def test_request_and_response_byte_limits_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(entrypoint, "MAX_REQUEST_BYTES", 32)
    result = entrypoint.score_payload(b" " * 33)
    assert result["scorer_status"] == "invalid_input"
    assert result["error"] == "request_too_large"

    monkeypatch.setattr(entrypoint, "MAX_RESPONSE_BYTES", 64)
    with pytest.raises(OverflowError):
        entrypoint._encode_result(
            entrypoint._result(
                action="score",
                status="mock",
                error=None,
                report={INSTANCE_ID: {"resolved": True}},
                resolved=True,
            )
        )
