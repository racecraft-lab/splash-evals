"""Prepare frozen SWE-bench eval scripts and score captured logs in a trusted image."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import importlib.metadata
import json
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

SCHEMA_VERSION: Final = 1
SWEBENCH_VERSION: Final = "5.0.2"
MAX_REQUEST_BYTES: Final = 160 * 1024 * 1024
MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
MAX_PATCH_BYTES: Final = 64 * 1024 * 1024
MAX_LOG_BYTES: Final = 64 * 1024 * 1024
MAX_EVAL_SCRIPT_BYTES: Final = 4 * 1024 * 1024
MAX_TEST_CASES: Final = 10_000

_COMMON_KEYS: Final = frozenset(
    {"schema_version", "action", "swebench_version", "instance_id", "test_spec"}
)
_SCORE_KEYS: Final = frozenset(
    {
        "test_spec_sha256",
        "eval_script_sha256",
        "patch",
        "patch_sha256",
        "grading_log_b64",
        "grading_log_sha256",
        "host_cleanup_confirmed",
    }
)
_TEST_SPEC_KEYS: Final = frozenset(
    {
        "instance_id",
        "image",
        "repo",
        "version",
        "FAIL_TO_PASS",
        "PASS_TO_PASS",
        "log_parser",
        "eval_type",
        "eval_script",
        "image_assets",
    }
)
_EVAL_TYPES: Final = frozenset({"pass_and_fail", "fail_only"})
_DIGEST_LENGTH: Final = 64


class _InputError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code


class _ScorerUnavailable(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code


@dataclass(frozen=True, slots=True)
class _Request:
    action: str
    instance_id: str
    test_spec: dict[str, object]
    test_spec_sha256: str
    expected_eval_script_sha256: str | None = None
    patch: str | None = None
    patch_sha256: str | None = None
    grading_log: bytes | None = None
    grading_log_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class _ScorerFunctions:
    make_test_spec: Callable[[dict[str, object]], object]
    get_eval_report: Callable[[object, dict[str, str], str, bool], object]
    log_parser_names: frozenset[str]


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _InputError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise _InputError("invalid_json_number")


def _text(value: object, limit: int, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not allow_empty and not value):
        raise _InputError("invalid_text_field")
    try:
        raw = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise _InputError("invalid_unicode") from None
    if len(raw) > limit:
        raise _InputError("text_field_too_large")
    return value


def _digest(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != _DIGEST_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _InputError("invalid_digest")
    return value


def _canonical_test_spec(spec: dict[str, object]) -> bytes:
    try:
        return json.dumps(
            spec,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise _InputError("invalid_test_spec") from None


def _parse_request(raw: bytes) -> _Request:
    if len(raw) > MAX_REQUEST_BYTES:
        raise _InputError("request_too_large")
    try:
        document = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except _InputError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise _InputError("invalid_json") from None
    if type(document) is not dict or not _COMMON_KEYS.issubset(document):
        raise _InputError("invalid_request_schema")
    action = document["action"]
    if type(action) is not str or action not in {"prepare", "score"}:
        raise _InputError("unsupported_action")
    expected_keys = _COMMON_KEYS | (_SCORE_KEYS if action == "score" else frozenset())
    if frozenset(document) != expected_keys:
        raise _InputError("invalid_request_schema")
    if type(document["schema_version"]) is not int or document["schema_version"] != SCHEMA_VERSION:
        raise _InputError("unsupported_schema_version")
    if document["swebench_version"] != SWEBENCH_VERSION:
        raise _InputError("unsupported_swebench_version")

    instance_id = _text(document["instance_id"], 512)
    spec = document["test_spec"]
    if type(spec) is not dict or frozenset(spec) != _TEST_SPEC_KEYS:
        raise _InputError("invalid_test_spec_schema")
    if spec["instance_id"] != instance_id:
        raise _InputError("instance_id_mismatch")
    _text(spec["instance_id"], 512)
    _text(spec["image"], 4096)
    _text(spec["repo"], 512)
    _text(spec["version"], 128)
    _text(spec["log_parser"], 128)
    if _text(spec["eval_type"], 64) not in _EVAL_TYPES:
        raise _InputError("unsupported_eval_type")
    script = _text(spec["eval_script"], MAX_EVAL_SCRIPT_BYTES, allow_empty=True)
    if "\x00" in script:
        raise _InputError("unsafe_test_spec")
    for key in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        cases = spec[key]
        if type(cases) is not list or len(cases) > MAX_TEST_CASES:
            raise _InputError("invalid_test_case_list")
        for case in cases:
            _text(case, 4096)
    # image_assets is an optional, host-frozen SWE-bench field. Preserve its
    # exact JSON value for make_test_spec; it is never interpreted here.
    if spec["image_assets"] is not None and type(spec["image_assets"]) not in (
        list,
        dict,
    ):
        raise _InputError("unsupported_image_assets")
    spec = dict(spec)
    spec_sha256 = hashlib.sha256(_canonical_test_spec(spec)).hexdigest()

    if action == "prepare":
        return _Request(action, instance_id, spec, spec_sha256)
    if not hmac.compare_digest(_digest(document["test_spec_sha256"]), spec_sha256):
        raise _InputError("test_spec_digest_mismatch")
    eval_sha256 = _digest(document["eval_script_sha256"])
    if type(document["host_cleanup_confirmed"]) is not bool or not document[
        "host_cleanup_confirmed"
    ]:
        raise _InputError("host_cleanup_not_confirmed")

    patch = _text(document["patch"], MAX_PATCH_BYTES, allow_empty=True)
    patch_sha256 = _digest(document["patch_sha256"])
    if not hmac.compare_digest(hashlib.sha256(patch.encode("utf-8")).hexdigest(), patch_sha256):
        raise _InputError("patch_digest_mismatch")
    encoded_log = document["grading_log_b64"]
    if type(encoded_log) is not str or len(encoded_log) > 4 * ((MAX_LOG_BYTES + 2) // 3):
        raise _InputError("invalid_grading_log")
    try:
        grading_log = base64.b64decode(encoded_log, validate=True)
        grading_log.decode("utf-8", errors="strict")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise _InputError("invalid_grading_log") from None
    if len(grading_log) > MAX_LOG_BYTES:
        raise _InputError("grading_log_too_large")
    log_sha256 = _digest(document["grading_log_sha256"])
    if not hmac.compare_digest(hashlib.sha256(grading_log).hexdigest(), log_sha256):
        raise _InputError("grading_log_digest_mismatch")
    return _Request(
        action,
        instance_id,
        spec,
        spec_sha256,
        eval_sha256,
        patch,
        patch_sha256,
        grading_log,
        log_sha256,
    )


def _load_official_scorer() -> _ScorerFunctions:
    try:
        version = importlib.metadata.version("swebench")
    except importlib.metadata.PackageNotFoundError:
        raise _ScorerUnavailable("swebench_unavailable") from None
    except Exception:
        raise _ScorerUnavailable("swebench_version_unavailable") from None
    if version != SWEBENCH_VERSION:
        raise _ScorerUnavailable("swebench_version_mismatch")
    try:
        from swebench.harness.grading import get_eval_report
        from swebench.harness.log_parsers import PARSER_REGISTRY
        from swebench.harness.utils import make_test_spec
    except Exception:
        raise _ScorerUnavailable("swebench_import_unavailable") from None
    if not callable(make_test_spec) or not callable(get_eval_report) or not PARSER_REGISTRY:
        raise _ScorerUnavailable("swebench_api_unavailable")
    return _ScorerFunctions(make_test_spec, get_eval_report, frozenset(PARSER_REGISTRY))


def _entrypoint_sha256() -> str:
    try:
        return hashlib.sha256(Path(__file__).resolve(strict=True).read_bytes()).hexdigest()
    except OSError:
        raise _ScorerUnavailable("entrypoint_unavailable") from None


def _result(
    action: str | None,
    status: str,
    error: str | None,
    *,
    request: _Request | None = None,
    eval_script_b64: str | None = None,
    eval_script_sha256: str | None = None,
    scorer_entrypoint_sha256: str | None = None,
    resolved: bool | None = None,
    report: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "action": action,
        "scorer_status": status,
        "error": error,
        "swebench_version": (
            SWEBENCH_VERSION if status in {"prepared", "official"} else None
        ),
        "instance_id": request.instance_id if request else None,
        "test_spec_sha256": request.test_spec_sha256 if request else None,
        "eval_script_b64": eval_script_b64,
        "eval_script_sha256": eval_script_sha256,
        "scorer_entrypoint_sha256": scorer_entrypoint_sha256,
        "patch_sha256": request.patch_sha256 if request else None,
        "grading_log_sha256": request.grading_log_sha256 if request else None,
        # This echoes a host assertion; the scorer does not independently attest cleanup.
        "host_cleanup_confirmed": (
            True if request and request.action == "score" else None
        ),
        "resolved": resolved,
        "report": report,
    }


def score_payload(
    raw: bytes,
    *,
    _test_functions: _ScorerFunctions | None = None,
) -> dict[str, object]:
    """Run prepare or score. Injected functions are always reported as mock."""
    try:
        request = _parse_request(raw)
    except _InputError as exc:
        return _result(None, "invalid_input", exc.code)
    try:
        entrypoint_sha256 = _entrypoint_sha256()
        functions = _test_functions or _load_official_scorer()
    except _ScorerUnavailable as exc:
        return _result(request.action, "dependency_unavailable", exc.code, request=request)

    if request.test_spec["log_parser"] not in functions.log_parser_names:
        return _result(
            request.action, "invalid_input", "unsupported_log_parser", request=request
        )
    try:
        test_spec = functions.make_test_spec(dict(request.test_spec))
        if (
            getattr(test_spec, "instance_id", None) != request.instance_id
            or getattr(test_spec, "image", None) != request.test_spec["image"]
        ):
            raise ValueError("test_spec_identity_mismatch")
        eval_script = getattr(test_spec, "eval_script", None)
        if type(eval_script) is not str or not eval_script:
            raise ValueError("invalid_eval_script")
        eval_script_bytes = eval_script.encode("utf-8", errors="strict")
        if len(eval_script_bytes) > MAX_EVAL_SCRIPT_BYTES:
            return _result(
                request.action,
                "scorer_error",
                "eval_script_too_large",
                request=request,
                scorer_entrypoint_sha256=entrypoint_sha256,
            )
        eval_script_sha256 = hashlib.sha256(eval_script_bytes).hexdigest()
        if request.action == "prepare":
            status = "mock" if _test_functions is not None else "prepared"
            return _result(
                request.action,
                status,
                None,
                request=request,
                eval_script_b64=base64.b64encode(eval_script_bytes).decode("ascii"),
                eval_script_sha256=eval_script_sha256,
                scorer_entrypoint_sha256=entrypoint_sha256,
            )
        if not hmac.compare_digest(
            eval_script_sha256, request.expected_eval_script_sha256 or ""
        ):
            return _result(
                request.action,
                "invalid_input",
                "eval_script_digest_mismatch",
                request=request,
                eval_script_sha256=eval_script_sha256,
                scorer_entrypoint_sha256=entrypoint_sha256,
            )

        prediction = {
            "instance_id": request.instance_id,
            "model_name_or_path": "local-evals/trusted-control-scorer",
            "model_patch": request.patch or "",
        }
        if request.grading_log is None:
            raise ValueError("missing_validated_grading_log")
        with tempfile.TemporaryDirectory(prefix="swebench-score-") as directory:
            log_path = Path(directory) / "grading.log"
            with log_path.open("xb") as stream:
                stream.write(request.grading_log)
            # SWE-bench 5.0.2 signature: (test_spec, prediction,
            # test_log_path, include_tests_status). Called once after cleanup.
            raw_report = functions.get_eval_report(test_spec, prediction, str(log_path), True)
        if type(raw_report) is not dict or frozenset(raw_report) != {request.instance_id}:
            raise ValueError("invalid_official_report")
        report = raw_report
        entry = report[request.instance_id]
        if type(entry) is not dict or type(entry.get("resolved")) is not bool:
            raise ValueError("invalid_official_report")
        report_bytes = json.dumps(report, allow_nan=False, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(report_bytes) > MAX_RESPONSE_BYTES:
            raise OverflowError("official_report_too_large")
    except OverflowError:
        return _result(
            request.action,
            "scorer_error",
            "official_report_too_large",
            request=request,
            eval_script_sha256=eval_script_sha256,
            scorer_entrypoint_sha256=entrypoint_sha256,
        )
    except Exception:
        return _result(
            request.action,
            "scorer_error",
            "official_scorer_failed",
            request=request,
            eval_script_sha256=eval_script_sha256,
            scorer_entrypoint_sha256=entrypoint_sha256,
        )

    status = "mock" if _test_functions is not None else "official"
    return _result(
        request.action,
        status,
        None,
        request=request,
        eval_script_sha256=eval_script_sha256,
        scorer_entrypoint_sha256=entrypoint_sha256,
        resolved=entry["resolved"],
        report=report,
    )


def _encode_result(result: Mapping[str, object]) -> bytes:
    encoded = json.dumps(
        result, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise OverflowError("result_too_large")
    return encoded


def main() -> int:
    result = score_payload(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1))
    try:
        encoded = _encode_result(result)
    except OverflowError:
        result = {
            **result,
            "scorer_status": "scorer_error",
            "error": "result_too_large",
            "eval_script_b64": None,
            "resolved": None,
            "report": None,
        }
        encoded = _encode_result(result)
    sys.stdout.buffer.write(encoded + b"\n")
    sys.stdout.buffer.flush()
    return 0 if result["scorer_status"] in {"prepared", "official"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
