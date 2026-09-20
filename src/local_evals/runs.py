"""Run planning, local inference, scoring, resumption, and comparison.

All detailed evidence is written below ``LOCAL_EVALS_STATE_DIR`` (or the
platform-neutral private default), never below the source repository.
"""

from __future__ import annotations

import fcntl
import hashlib
import ipaddress
import json
import os
import re
import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from .benchmarks import execute_evalscope_core, inspect_core_readiness
from .lmstudio import LMStudioError, model_keys_equivalent
from .lmstudio import discover as discover_lmstudio
from .models import DiscoveryReport, LocalityStatus, ModelRecord, ModelRecordSource
from .preflight import LocalityError as PreflightLocalityError
from .preflight import require_verified_local
from .statistics import paired_binary_difference, summarize_binary


class RunError(RuntimeError):
    """Base class for an evaluation runner refusal or failure."""


class StateDirectoryError(RunError):
    """The raw evidence directory is unsafe or unavailable."""


class LocalityError(RunError):
    """An inference destination is not unambiguously loopback-only."""


class BudgetExceeded(RunError):
    """The shared live-session budget cannot accommodate another request."""


class ResumeRefused(RunError):
    """A run cannot resume under a changed protocol or fingerprint."""


class ScoringError(RunError):
    """A response cannot be parsed under the frozen scorer contract."""


@dataclass(frozen=True)
class Task:
    task_id: str
    family: str
    prompt: str
    scorer: str
    expected: Any
    base_task_id: str | None = None

    def public_manifest(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "base_task_id": self.base_task_id or self.task_id,
            "family": self.family,
            "scorer": self.scorer,
            "prompt_sha256": _sha256_text(self.prompt),
            "expected_sha256": _sha256_json(self.expected),
        }

    def private_manifest(self) -> dict[str, Any]:
        return {
            **self.public_manifest(),
            "prompt": self.prompt,
            "expected": self.expected,
        }


@dataclass(frozen=True)
class ModelAttribution:
    """Fail-closed selection and sanitized evidence for one loaded local instance."""

    model_id: str | None
    model_is_splash: bool
    locality_evidence: dict[str, Any]
    model_instance_evidence: dict[str, Any]
    blockers: tuple[str, ...]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return _sha256_text(payload)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_state_dir(root: Path | None = None, *, create: bool = True) -> Path:
    """Resolve a private state directory and refuse paths inside the repository."""
    repo = (root or project_root()).resolve()
    configured = os.environ.get("LOCAL_EVALS_STATE_DIR")
    candidate = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".local" / "state" / "splash-evals"
    )
    resolved = candidate.resolve(strict=False)
    if resolved == repo or repo in resolved.parents:
        raise StateDirectoryError("LOCAL_EVALS_STATE_DIR must be outside the source repository")
    if resolved.exists() and resolved.is_symlink():
        raise StateDirectoryError("LOCAL_EVALS_STATE_DIR must not be a symlink")
    if create:
        resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(resolved, 0o700)
    return resolved


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RunError(f"configuration not found: {path.name}")
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RunError(f"configuration must be a mapping: {path.name}")
    return value


def load_policies(root: Path | None = None) -> dict[str, Any]:
    return _load_yaml((root or project_root()) / "configs" / "policies.yaml")


def load_config(name: str, root: Path | None = None) -> dict[str, Any]:
    """Load a public config or a private frozen candidate without path traversal."""
    if not name or name != Path(name).name or any(part in name for part in ("/", "\\", "..")):
        raise RunError("config must be a simple identifier")
    repo = root or project_root()
    public_path = repo / "configs" / "profiles" / f"{name}.yaml"
    if public_path.is_file():
        return _load_yaml(public_path)
    private_path = get_state_dir(repo) / "frozen_profiles" / f"{name}.json"
    if private_path.is_file():
        value = json.loads(private_path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value
    raise RunError(f"unknown configuration: {name}")


def load_suite(name: str, root: Path | None = None) -> dict[str, Any]:
    if name != Path(name).name or any(part in name for part in ("/", "\\", "..")):
        raise RunError("suite must be a simple identifier")
    return _load_yaml((root or project_root()) / "configs" / "profiles" / f"{name}.yaml")


def validate_loopback_origin(origin: str) -> str:
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise LocalityError("LM Studio origin must be an http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LocalityError("LM Studio origin must not contain credentials, query, or fragment")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port)}
    except OSError as error:
        raise LocalityError("LM Studio host could not be resolved") from error
    if not addresses or any(not ipaddress.ip_address(address).is_loopback for address in addresses):
        raise LocalityError("LM Studio inference is restricted to loopback addresses")
    normalized = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        normalized += f":{parsed.port}"
    return normalized


def _builtin_tasks(suite: str) -> list[Task]:
    if suite == "smoke":
        raw = [
            ("smoke-arithmetic", "reasoning", "Return only the integer: 17 + 25", "exact", "42"),
            (
                "smoke-uppercase",
                "instruction",
                "Return only this word in uppercase: splash",
                "exact",
                "SPLASH",
            ),
            (
                "smoke-json",
                "structured",
                'Return exactly one JSON object: {"ok": true}',
                "json",
                {"ok": True},
            ),
            ("smoke-sort", "reasoning", "Sort 3, 1, 2. Return only: 1,2,3", "exact", "1,2,3"),
            ("smoke-negative", "reasoning", "Return only the integer: 5 - 12", "exact", "-7"),
            (
                "smoke-bool",
                "structured",
                'Return exactly one JSON object: {"value": false}',
                "json",
                {"value": False},
            ),
            ("smoke-token", "instruction", "Return only the token ALPHA-7.", "exact", "ALPHA-7"),
            (
                "smoke-count",
                "reasoning",
                "How many letters are in 'local'? Return only the integer.",
                "exact",
                "5",
            ),
            (
                "smoke-reverse",
                "instruction",
                "Reverse 'abcde'. Return only the result.",
                "exact",
                "edcba",
            ),
            (
                "smoke-schema",
                "structured",
                'Return JSON with integer field "n" equal to 9.',
                "json",
                {"n": 9},
            ),
            (
                "smoke-choice",
                "reasoning",
                "Which is larger, 0.8 or 0.75? Return only the larger number.",
                "exact",
                "0.8",
            ),
            ("smoke-finish", "instruction", "Return exactly: done", "exact", "done"),
        ]
    elif suite == "calibration":
        raw = []
        for index in range(1, 7):
            raw.append(
                (
                    f"cal-short-{index:02d}",
                    "short_answer",
                    f"Return only {index} squared.",
                    "exact",
                    str(index * index),
                )
            )
        for index in range(1, 7):
            a = index + 10
            b = index + 3
            raw.append(
                (
                    f"cal-reason-{index:02d}",
                    "reasoning",
                    f"Return only the integer: ({a} * 2) - {b}",
                    "exact",
                    str((a * 2) - b),
                )
            )
        for index in range(1, 7):
            raw.append(
                (
                    f"cal-json-{index:02d}",
                    "structured",
                    f'Return exactly JSON: {{"index": {index}, "valid": true}}',
                    "json",
                    {"index": index, "valid": True},
                )
            )
        for index in range(1, 7):
            expected = f"module_{index}.py"
            raw.append(
                (
                    f"cal-repo-{index:02d}",
                    "repository",
                    f"A traceback points to module_{index}.py line 4. Return only the filename.",
                    "exact",
                    expected,
                )
            )
    elif suite == "pilot":
        raw = [
            (
                "pilot-reason-01",
                "reasoning",
                "A box has 3 red and 2 blue tokens. Two red tokens are removed. "
                "How many tokens remain? Return only the integer.",
                "exact",
                "3",
            ),
            (
                "pilot-reason-02",
                "reasoning",
                "Sequence: 2, 6, 12, 20. Return only the next number.",
                "exact",
                "30",
            ),
            (
                "pilot-instruct-01",
                "instruction",
                "Return exactly three comma-separated lowercase words: alpha,beta,gamma",
                "exact",
                "alpha,beta,gamma",
            ),
            (
                "pilot-instruct-02",
                "instruction",
                "Return only the second item from: oak | pine | elm",
                "exact",
                "pine",
            ),
            (
                "pilot-json-01",
                "structured",
                'Return exactly JSON with fields "status"="ok" and "count"=3.',
                "json",
                {"status": "ok", "count": 3},
            ),
            (
                "pilot-json-02",
                "structured",
                'Return exactly JSON: {"items":[1,2],"complete":true}',
                "json",
                {"items": [1, 2], "complete": True},
            ),
            (
                "pilot-tool-01",
                "tool_contract",
                "A local calculator add tool receives integers a and b. Return exactly JSON "
                'arguments for 8+13 using keys "a" and "b".',
                "json",
                {"a": 8, "b": 13},
            ),
            (
                "pilot-tool-02",
                "tool_contract",
                "A file lookup tool needs a relative path. Return exactly JSON: "
                '{"path":"src/app.py"}',
                "json",
                {"path": "src/app.py"},
            ),
            (
                "pilot-context-01",
                "context",
                "Facts: A=violet; B=amber; C=cyan. Return only the value of B.",
                "exact",
                "amber",
            ),
            (
                "pilot-repo-01",
                "repository",
                "Files: app.py imports util.py; util.py imports schema.py. Which file is the "
                "transitive dependency of app.py? Return only the filename.",
                "exact",
                "schema.py",
            ),
        ]
    else:
        raise RunError(f"suite {suite!r} has no built-in executable task set")
    return [Task(*item) for item in raw]


def suite_tasks(suite: str) -> list[Task]:
    """Public pure API returning the frozen built-in selection."""
    return _builtin_tasks(suite)


def scorer_self_test_cases() -> list[tuple[Task, dict[str, Any], str]]:
    """Deterministic scorer fixtures; these are never model measurements."""
    json_task = Task("fixture-json", "harness", "synthetic", "json", {"ok": True})
    exact_task = Task("fixture-exact", "harness", "synthetic", "exact", "yes")
    return [
        (exact_task, {"content": "yes"}, "pass"),
        (exact_task, {"content": "no"}, "wrong_answer"),
        (exact_task, {"content": ""}, "missing_answer"),
        (json_task, {"content": '{"ok": true}'}, "pass"),
        (json_task, {"content": "not-json"}, "client_parser_error"),
        (json_task, {"content": '<think>{"ok": true}'}, "client_parser_error"),
        (json_task, {"content": '{"ok": false}'}, "schema_or_value_error"),
        (
            json_task,
            {"content": '{"ok": true', "finish_reason": "length"},
            "output_budget_exhaustion",
        ),
        (exact_task, {"content": None}, "missing_answer"),
        (exact_task, {"transport_error": "timeout"}, "transport_error"),
        (json_task, {"content": '{"ok":', "finish_reason": "stop"}, "client_parser_error"),
        (exact_task, {"interrupted": True}, "interrupted"),
    ]


def parse_and_score(task: Task, response: dict[str, Any]) -> dict[str, Any]:
    """Parse one response without cleanup that could hide protocol failures."""
    if response.get("interrupted"):
        return {"scorable": False, "score": None, "failure_type": "interrupted"}
    if response.get("transport_error"):
        return {"scorable": False, "score": None, "failure_type": "transport_error"}
    finish_reason = response.get("finish_reason")
    content = response.get("content")
    if content is None or not str(content).strip():
        return {"scorable": False, "score": None, "failure_type": "missing_answer"}
    if finish_reason == "length":
        return {"scorable": False, "score": None, "failure_type": "output_budget_exhaustion"}
    if task.scorer == "exact":
        passed = str(content).strip() == str(task.expected).strip()
        return {
            "scorable": True,
            "score": int(passed),
            "failure_type": None if passed else "wrong_answer",
            "extracted_answer": str(content).strip(),
        }
    if task.scorer == "json":
        try:
            parsed = json.loads(str(content))
        except json.JSONDecodeError:
            return {"scorable": False, "score": None, "failure_type": "client_parser_error"}
        passed = parsed == task.expected
        return {
            "scorable": True,
            "score": int(passed),
            "failure_type": None if passed else "schema_or_value_error",
            "extracted_answer": parsed,
        }
    raise ScoringError(f"unsupported scorer: {task.scorer}")


def assemble_tool_arguments(fragments: list[str]) -> dict[str, Any]:
    """Assemble streamed tool argument fragments and parse exactly one JSON object.

    No tags, prose, or prefixes are stripped. That fail-closed behavior keeps a
    leading ``<`` or a truncated tool payload visible as a client-parser error.
    """
    if not fragments or any(not isinstance(fragment, str) for fragment in fragments):
        raise ScoringError("client_parser_error: tool argument fragments must be strings")
    payload = "".join(fragments)
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ScoringError("client_parser_error: malformed or incomplete tool arguments") from error
    if not isinstance(parsed, dict):
        raise ScoringError("client_parser_error: tool arguments must decode to an object")
    return parsed


def parse_tool_call(response: dict[str, Any]) -> dict[str, Any]:
    """Parse a complete or explicitly fragmented synthetic tool-call envelope."""
    fragments = response.get("tool_argument_fragments")
    if isinstance(fragments, list):
        return assemble_tool_arguments(fragments)
    arguments = response.get("arguments")
    if isinstance(arguments, str):
        return assemble_tool_arguments([arguments])
    raise ScoringError("client_parser_error: missing tool arguments")


def run_scorer_self_test() -> dict[str, Any]:
    results = []
    for task, response, expected_failure in scorer_self_test_cases():
        result = parse_and_score(task, response)
        actual = "pass" if result.get("score") == 1 else result.get("failure_type")
        results.append(
            {
                "case": task.task_id,
                "expected": expected_failure,
                "actual": actual,
                "passed": actual == expected_failure,
            }
        )
    return {
        "evidence_class": "synthetic_mock",
        "namespace": "harness-only",
        "eligible_for_capability_report": False,
        "passed": all(item["passed"] for item in results),
        "case_count": len(results),
        "results": results,
    }


def _config_origin(config: dict[str, Any]) -> str:
    server = config.get("server") or {}
    origin = server.get("origin")
    if not isinstance(origin, str):
        raise RunError("configuration has no LM Studio server.origin")
    return validate_loopback_origin(origin)


def _config_openai_base_url(config: dict[str, Any]) -> str:
    origin = _config_origin(config)
    expected = f"{origin}/v1"
    configured = (config.get("server") or {}).get("openai_base_url")
    if configured != expected:
        raise RunError("configuration must pin the loopback LM Studio OpenAI API root at /v1")
    return expected


def _api_key(config: dict[str, Any]) -> str | None:
    env_name = (config.get("server") or {}).get("api_key_env")
    return os.environ.get(env_name) if isinstance(env_name, str) else None


def _splash_identity_evidence(
    selected: ModelRecord, discovered: tuple[ModelRecord, ...]
) -> tuple[bool, list[str], ModelRecord | None]:
    """Require exact CLI selection plus one same-key native identity record."""
    native_matches = [
        model
        for model in discovered
        if model.source is ModelRecordSource.NATIVE_REST
        and model_keys_equivalent(selected.key, model.key, model.publisher)
        and selected.instance_id in model.loaded_instance_ids
    ]
    if selected.source is not ModelRecordSource.LMS_CLI_LOADED or len(native_matches) != 1:
        return False, [], None
    native = native_matches[0]
    identity_fields = {
        "model_key": native.key,
        "display_name": native.display_name,
        "publisher": native.publisher,
        "architecture": native.architecture,
        "selected_variant": native.selected_variant,
    }
    splash_sources = {
        name for name, value in identity_fields.items() if value and "splash" in value.casefold()
    }
    qwen_sources = {
        name
        for name, value in identity_fields.items()
        if value and re.search(r"qwen[\s._/-]*3[\s._/-]*8(?:\D|$)", value, re.IGNORECASE)
    }
    corroborating_sources = splash_sources | qwen_sources
    confirmed = bool(splash_sources and qwen_sources and len(corroborating_sources) >= 2)
    signals = [
        *(f"{name}:splash" for name in sorted(splash_sources)),
        *(f"{name}:qwen3.8" for name in sorted(qwen_sources)),
    ]
    return confirmed, signals, native


def _sanitized_locality(report: DiscoveryReport) -> dict[str, Any]:
    return {
        "status": report.locality.status.value,
        "endpoint_loopback": report.locality.endpoint_loopback,
        "lm_link_state": report.locality.lm_link_state,
        "local_instance_evidence": report.locality.local_instance_evidence,
        "reasons": list(report.locality.reasons),
    }


def _discover_attribution_report(
    config: dict[str, Any], requested: object
) -> tuple[DiscoveryReport, list[str]]:
    blockers: list[str] = []
    origin = _config_origin(config)
    api_key_env = (config.get("server") or {}).get("api_key_env", "LM_STUDIO_API_KEY")
    if not isinstance(api_key_env, str):
        blockers.append("LM Studio API key environment reference is invalid.")
        api_key_env = "LM_STUDIO_API_KEY"
    report = discover_lmstudio(
        origin,
        api_key_env=api_key_env,
        selected_model=str(requested) if requested else None,
    )
    return report, blockers


def _locality_blockers(report: DiscoveryReport) -> list[str]:
    blockers = [
        f"LM Studio discovery blocker: {issue.source}/{issue.code}."
        for issue in report.issues
        if issue.blocking
    ]
    try:
        require_verified_local(report.locality)
    except PreflightLocalityError:
        blockers.append("Selected model execution is not verified local; inference is refused.")
    return blockers


def _select_loaded_model(
    report: DiscoveryReport, requested: object
) -> tuple[ModelRecord | None, list[str]]:
    loaded = [
        candidate
        for candidate in report.models
        if candidate.loaded and candidate.source is ModelRecordSource.LMS_CLI_LOADED
    ]
    matches = (
        [
            candidate
            for candidate in loaded
            if str(requested) in {candidate.key, candidate.instance_id}
        ]
        if requested
        else loaded
    )
    if not matches:
        return None, ["The selected model is not a discovered loaded LM Studio instance."]
    if len(matches) > 1:
        return None, [
            "Model selection is ambiguous across loaded instances; select an exact "
            "instance identifier."
        ]
    selected = matches[0]
    if not selected.instance_id:
        return selected, [
            "The selected model lacks a concrete loaded instance identifier; inference is refused."
        ]
    return selected, []


def _instance_evidence(
    selected: ModelRecord,
    native: ModelRecord | None,
    model_is_splash: bool,
    signals: list[str],
    report: DiscoveryReport,
) -> dict[str, Any]:
    return {
        "selection": "exact_loaded_record",
        "model_key": selected.key,
        "instance_id_sha256": _sha256_text(selected.instance_id)
        if selected.instance_id is not None
        else None,
        "selected_variant": selected.selected_variant,
        "file_revision": selected.file_revision,
        "engine": selected.engine,
        "engine_version": selected.engine_version,
        "cli_version": report.cli_version,
        "app_version": report.app_version,
        "native_identity": (
            {
                "source": native.source.value,
                "model_key": native.key,
                "display_name": native.display_name,
                "publisher": native.publisher,
                "architecture": native.architecture,
                "format": native.model_format,
                "model_type": native.model_type,
                "quantization": native.quantization,
                "selected_variant": native.selected_variant,
                "loaded_instance_id_match": selected.instance_id in native.loaded_instance_ids,
                "reasoning_allowed": list(native.reasoning_allowed),
                "reasoning_default": native.reasoning_default,
            }
            if native is not None
            else None
        ),
        "splash_attribution": "confirmed" if model_is_splash else "not_confirmed",
        "attribution_signal_fields": signals,
    }


def _resolve_model(config: dict[str, Any]) -> ModelAttribution:
    model = config.get("model") or {}
    requested = model.get("instance_id") or model.get("key") or os.environ.get("LOCAL_EVALS_MODEL")
    try:
        report, blockers = _discover_attribution_report(config, requested)
    except (LMStudioError, OSError, ValueError, RunError) as error:
        return ModelAttribution(
            model_id=None,
            model_is_splash=False,
            locality_evidence={"status": "unknown", "verified": False},
            model_instance_evidence={"selection": "unresolved"},
            blockers=(f"LM Studio discovery failed: {type(error).__name__}",),
        )
    blockers.extend(_locality_blockers(report))
    selected, selection_blockers = _select_loaded_model(report, requested)
    blockers.extend(selection_blockers)
    locality_evidence = _sanitized_locality(report)
    if selected is None:
        return ModelAttribution(
            model_id=None,
            model_is_splash=False,
            locality_evidence=locality_evidence,
            model_instance_evidence={"selection": "unresolved", "requested": bool(requested)},
            blockers=tuple(blockers),
        )
    identity_match, signals, native = _splash_identity_evidence(selected, report.models)
    model_is_splash = identity_match and report.locality.status is LocalityStatus.VERIFIED_LOCAL
    return ModelAttribution(
        model_id=selected.instance_id,
        model_is_splash=model_is_splash,
        locality_evidence=locality_evidence,
        model_instance_evidence=_instance_evidence(
            selected, native, model_is_splash, signals, report
        ),
        blockers=tuple(blockers),
    )


def _historical_protocol(
    suite: str,
    profile: dict[str, Any],
    *,
    sample_count: int,
    output_budget: int,
    reasoning_mode: str | None,
) -> dict[str, Any]:
    """Emit the complete comparator contract without inventing unknown facts."""
    practical = suite == "pilot"
    selection = _selection_evidence(profile)
    return {
        "benchmark_name": "Racecraft practical task set" if practical else profile.get("task_set"),
        "benchmark_version": profile.get("task_set"),
        "split": "held_out" if selection["held_out"] else None,
        "dataset_revision": profile.get("task_set"),
        "sample_id_manifest": selection["ordered_sample_manifest_sha256"],
        "metric_name": "accuracy",
        "metric_unit": "proportion",
        "sample_count": sample_count,
        "few_shot": 0,
        "prompts_or_template_revision": profile.get("task_set"),
        "reasoning_mode": reasoning_mode,
        "output_budget": output_budget,
        "attempts_per_task": int(profile.get("repetitions", 1)),
        "aggregation": "mean_binary_score",
        "tool_access": "none",
        "agent_scaffold_revision": None,
        "scorer_revision": profile.get("scorer_version"),
        "answer_extraction": "builtin_strict",
        "higher_is_better": True,
    }


def _selection_evidence(profile: dict[str, Any]) -> dict[str, Any]:
    evidence = profile.get("selection_evidence")
    value = evidence if isinstance(evidence, dict) else {}
    sample_manifest = value.get("ordered_sample_manifest_sha256")
    contamination_revision = value.get("contamination_review_revision")
    valid_manifest = isinstance(sample_manifest, str) and bool(
        re.fullmatch(r"[0-9a-f]{64}", sample_manifest)
    )
    immutable = value.get("frozen_before_tuning") is True
    contamination = isinstance(contamination_revision, str) and bool(contamination_revision.strip())
    held_out = valid_manifest and immutable and contamination
    return {
        "held_out": held_out,
        "status": "held_out_verified" if held_out else "post_hoc_exploratory",
        "ordered_sample_manifest_sha256": sample_manifest if valid_manifest else None,
        "contamination_review_revision": contamination_revision if contamination else None,
        "frozen_before_tuning": immutable,
    }


def _reasoning_evidence(
    config: dict[str, Any], model_instance_evidence: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    control = config.get("reasoning_control") or {}
    transmitted = control.get("transmitted")
    blockers: list[str] = []
    if transmitted not in {"off", "low", "medium", "high", "on"}:
        blockers.append("An explicit native-v1 reasoning setting is required.")
        transmitted = None
    native = model_instance_evidence.get("native_identity") or {}
    supported = native.get("reasoning_allowed") or []
    if transmitted is not None and supported and transmitted not in supported:
        blockers.append("The requested reasoning setting is not exposed by the selected model.")
    return {
        "requested": control.get("desired_effort") or control.get("desired_mode"),
        "transmitted": transmitted,
        "supported_options": supported,
        "default": native.get("reasoning_default"),
        "effective_status": "not_attempted",
    }, blockers


def _runtime_evidence(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "transport": "lmstudio_native_v1",
        "endpoint": "/api/v1/chat",
        "cli_version": model.get("cli_version"),
        "app_version": model.get("app_version"),
        "engine": model.get("engine"),
        "engine_version": model.get("engine_version"),
    }


def _comparison_evidence(
    suite: str,
    profile: dict[str, Any],
    model: dict[str, Any],
    reasoning: dict[str, Any],
    *,
    sample_count: int,
    output_budget: int,
) -> dict[str, Any]:
    return {
        "historical_protocol": _historical_protocol(
            suite,
            profile,
            sample_count=sample_count,
            output_budget=output_budget,
            reasoning_mode=reasoning["transmitted"],
        ),
        "historical_comparison_eligibility": {
            "eligible_for_frontier_deltas": False,
            "reason": "Practical pilot is not an identical historical frontier benchmark."
            if suite == "pilot"
            else "No exact historical protocol match has been established.",
        },
        "reasoning_evidence": reasoning,
        "runtime_evidence": _runtime_evidence(model),
    }


def _suite_plan_metadata(
    suite: str,
    profile: dict[str, Any],
    config: dict[str, Any],
    repo: Path,
    tasks: list[Task],
) -> dict[str, Any]:
    public_tasks = [task.public_manifest() for task in tasks]
    metadata: dict[str, Any] = {
        "runner": "builtin-local",
        "core_readiness": None,
        "sample_count": len(tasks),
        "blockers": [],
        "output_directory": get_state_dir(repo, create=False) / "runs",
        "selection_hash": _sha256_json(public_tasks),
    }
    if suite != "core":
        return metadata
    readiness = inspect_core_readiness(
        profile,
        repo=repo,
        state=get_state_dir(repo, create=False),
        server_origin=_config_openai_base_url(config),
    )
    readiness_metadata = readiness.get("metadata", {})
    metadata.update(
        runner=str(readiness_metadata.get("runner", "evalscope-1.12")),
        core_readiness=readiness,
        sample_count=int(readiness_metadata.get("total_samples", 0)),
        blockers=list(readiness.get("blockers", [])),
        output_directory="external-state://runs",
        selection_hash=readiness_metadata.get("manifest_set_sha256"),
    )
    return metadata


def _planned_output_directory(base: Path | str, experiment_id: str) -> str:
    suffix = f"{experiment_id}-RUN_TIMESTAMP"
    return str(base / suffix) if isinstance(base, Path) else f"{base}/{suffix}"


def build_plan(
    suite: str,
    config_name: str,
    *,
    allow_expanded: bool = False,
    root: Path | None = None,
    resolve_live_model: bool = True,
) -> dict[str, Any]:
    repo = root or project_root()
    profile = load_suite(suite, repo)
    config = load_config(config_name, repo)
    policies = load_policies(repo)
    expanded = bool(profile.get("expanded"))
    blockers = list(profile.get("blockers") or [])
    if expanded and not allow_expanded:
        blockers.append("Expanded suite requires explicit --allow-expanded authorization.")
    tasks: list[Task] = []
    if suite in {"smoke", "calibration", "pilot"}:
        tasks = suite_tasks(suite)
    else:
        blockers.append(
            "No licensed/version-pinned executable task manifest is installed for this "
            "expanded suite."
        )
    model: str | None = None
    model_is_splash = False
    locality_evidence: dict[str, Any] = {"status": "unverified", "verified": False}
    model_instance_evidence: dict[str, Any] = {"selection": "unresolved"}
    if resolve_live_model:
        attribution = _resolve_model(config)
        model = attribution.model_id
        model_is_splash = attribution.model_is_splash
        locality_evidence = attribution.locality_evidence
        model_instance_evidence = attribution.model_instance_evidence
        blockers.extend(attribution.blockers)
    else:
        model = (config.get("model") or {}).get("key") or os.environ.get("LOCAL_EVALS_MODEL")
        blockers.append(
            "Live LM Studio locality and concrete model-instance attribution are unresolved."
        )
    repetitions = int(profile.get("repetitions", 1))
    request_count = len(tasks) * repetitions
    output_per_request = int(profile.get("max_output_tokens", 512))
    token_allowance = request_count * output_per_request
    output_root = get_state_dir(repo, create=False) / "runs"
    public_tasks = [task.public_manifest() for task in tasks]
    reasoning_evidence, reasoning_blockers = _reasoning_evidence(config, model_instance_evidence)
    blockers.extend(reasoning_blockers)
    protocol = {
        "suite": suite,
        "task_set": profile.get("task_set"),
        "scorer_version": profile.get("scorer_version"),
        "repetitions": repetitions,
        "max_output_tokens": output_per_request,
        "transport_retries": 0,
        "response_cache": "disabled",
    }
    comparison_evidence = _comparison_evidence(
        suite,
        profile,
        model_instance_evidence,
        reasoning_evidence,
        sample_count=len(tasks),
        output_budget=output_per_request,
    )
    selection_evidence = _selection_evidence(profile)
    experiment_id = _sha256_json(
        {
            "config": config,
            "model": model,
            "locality": locality_evidence,
            "model_instance": model_instance_evidence,
            "protocol": protocol,
            "tasks": public_tasks,
        }
    )[:20]
    return {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "suite": suite,
        "evidence_class": profile.get("evidence_class"),
        "config_id": config_name,
        "model_id": model,
        "model_is_splash": model_is_splash,
        "locality_evidence": locality_evidence,
        "model_instance_evidence": model_instance_evidence,
        "primary_objective_status_if_run": "pilot_only"
        if model_is_splash and suite == "pilot" and not blockers
        else "blocked",
        "protocol": protocol,
        **comparison_evidence,
        "tasks": public_tasks,
        "sample_count": len(tasks),
        "request_count": request_count,
        "estimated_max_generated_tokens": token_allowance,
        "limits": policies.get("initial_run_limits", {}),
        "blockers": sorted(set(blockers)),
        "output_directory": str(output_root / f"{experiment_id}-RUN_TIMESTAMP"),
        "forecast": None,
        "forecast_note": "Runtime forecast withheld until relevant local pilot measurements exist.",
        "limitations": profile.get("limitations", []),
        "allow_expanded": allow_expanded,
        "held_out": selection_evidence["held_out"],
        "selection_status": selection_evidence["status"],
        "selection_evidence": selection_evidence,
        "selection_hash": _sha256_json(public_tasks),
    }


def build_execution_plan(
    suite: str,
    config_name: str,
    *,
    allow_expanded: bool = False,
    root: Path | None = None,
    resolve_live_model: bool = True,
) -> dict[str, Any]:
    """Build the public plan, adding private-manifest readiness only for core."""
    plan = build_plan(
        suite,
        config_name,
        allow_expanded=allow_expanded,
        root=root,
        resolve_live_model=resolve_live_model,
    )
    if suite != "core":
        return plan
    repo = root or project_root()
    profile = load_suite(suite, repo)
    config = load_config(config_name, repo)
    metadata = _suite_plan_metadata(suite, profile, config, repo, [])
    readiness = metadata["core_readiness"]
    readiness_metadata = readiness.get("metadata", {})
    sample_count = int(metadata["sample_count"])
    blockers = [
        blocker
        for blocker in plan["blockers"]
        if not blocker.startswith("No licensed/version-pinned executable task manifest")
    ]
    blockers.extend(metadata["blockers"])
    comparison = _comparison_evidence(
        suite,
        profile,
        plan["model_instance_evidence"],
        plan["reasoning_evidence"],
        sample_count=sample_count,
        output_budget=int(profile.get("max_output_tokens", 512)),
    )
    ready = readiness.get("status") == "ready"
    selection_evidence = {
        "status": "frozen_held_out" if ready else "blocked",
        "manifest_set_sha256": readiness_metadata.get("manifest_set_sha256"),
        "family_evidence": readiness_metadata.get("family_evidence", {}),
    }
    experiment_id = _sha256_json(
        {
            "config": config,
            "model": plan["model_id"],
            "locality": plan["locality_evidence"],
            "model_instance": plan["model_instance_evidence"],
            "protocol": plan["protocol"],
            "core_readiness": readiness,
        }
    )[:20]
    plan.update(
        experiment_id=experiment_id,
        runner=metadata["runner"],
        core_readiness=readiness,
        sample_count=sample_count,
        request_count=sample_count,
        estimated_max_generated_tokens=sample_count * int(profile.get("max_output_tokens", 512)),
        blockers=sorted(set(blockers)),
        output_directory=_planned_output_directory(metadata["output_directory"], experiment_id),
        held_out=ready,
        selection_status=selection_evidence["status"],
        selection_evidence=selection_evidence,
        selection_hash=metadata["selection_hash"],
        primary_objective_status_if_run=(
            "capability_measurement"
            if ready and plan["model_is_splash"] and not blockers
            else "blocked"
        ),
        **comparison,
    )
    return plan


def _session_ledger(state: Path, limits: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    session_id = os.environ.get("LOCAL_EVALS_SESSION_ID", "initial")
    if session_id != Path(session_id).name or any(
        value in session_id for value in ("/", "\\", "..")
    ):
        raise RunError("LOCAL_EVALS_SESSION_ID must be a simple identifier")
    path = state / "sessions" / f"{session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_file():
        ledger = json.loads(path.read_text(encoding="utf-8"))
    else:
        ledger = {
            "schema_version": 1,
            "session_id": session_id,
            "created_at": datetime.now(UTC).isoformat(),
            "created_epoch": time.time(),
            "live_requests": 0,
            "generated_tokens": 0,
            "limits": limits,
        }
    return path, ledger


@contextmanager
def _locked_session_ledger(
    state: Path, limits: dict[str, Any]
) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Serialize the budget check, local request, and ledger update across processes."""
    ledger_path, _ = _session_ledger(state, limits)
    lock_path = ledger_path.with_suffix(".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield _session_ledger(state, limits)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _check_budget(ledger: dict[str, Any], requested_output_tokens: int) -> None:
    limits = ledger["limits"]
    if int(ledger["live_requests"]) + 1 > int(limits["max_live_requests"]):
        raise BudgetExceeded("shared session live-request limit reached")
    if int(ledger["generated_tokens"]) + requested_output_tokens > int(
        limits["max_generated_tokens"]
    ):
        raise BudgetExceeded("shared session generated-token allowance would be exceeded")
    elapsed_minutes = (time.time() - float(ledger["created_epoch"])) / 60.0
    if elapsed_minutes >= float(limits["max_wall_minutes"]):
        raise BudgetExceeded("shared session wall-time limit reached")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def _append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")
    os.chmod(path, 0o600)


def _chat_once(
    origin: str,
    api_key: str | None,
    model: str,
    prompt: str,
    settings: dict[str, Any],
) -> tuple[dict[str, Any], float]:
    if os.environ.get("LOCAL_EVALS_TEST_MODE") == "mock-only":
        raise RunError("live inference is disabled while LOCAL_EVALS_TEST_MODE=mock-only")
    safe_origin = validate_loopback_origin(origin)
    body: dict[str, Any] = {
        "model": model,
        "input": prompt,
        "stream": False,
        "store": False,
    }
    for key in ("temperature", "top_p", "top_k", "max_output_tokens", "reasoning"):
        value = settings.get(key)
        if value is not None:
            body[key] = value
    if body.get("reasoning") not in {"off", "low", "medium", "high", "on"}:
        raise RunError("an explicit supported reasoning setting is required")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=180.0, follow_redirects=False, trust_env=False) as client:
            response = client.post(f"{safe_origin}/api/v1/chat", json=body, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException as error:
        raise RunError("LM Studio request timed out; no retry was attempted") from error
    except (httpx.HTTPError, ValueError) as error:
        raise RunError(
            f"LM Studio request failed; no retry was attempted: {type(error).__name__}"
        ) from error
    elapsed = time.perf_counter() - started
    if not isinstance(payload, dict):
        raise RunError("LM Studio response was not a JSON object")
    return payload, elapsed


def _response_view(payload: dict[str, Any], expected_instance_id: str) -> dict[str, Any]:
    served = payload.get("model_instance_id")
    if not isinstance(served, str):
        return {
            "content": None,
            "finish_reason": None,
            "protocol_error": "missing_served_model_instance",
            "served_instance_match": False,
            "response_instance_id_sha256": None,
        }
    served_hash = _sha256_text(served)
    if served != expected_instance_id:
        return {
            "content": None,
            "finish_reason": None,
            "protocol_error": "served_model_instance_mismatch",
            "served_instance_match": False,
            "response_instance_id_sha256": served_hash,
        }
    output = payload.get("output")
    if not isinstance(output, list):
        return {
            "content": None,
            "finish_reason": None,
            "protocol_error": "missing_output",
            "served_instance_match": True,
            "response_instance_id_sha256": served_hash,
        }
    messages = [
        item.get("content")
        for item in output
        if isinstance(item, dict) and item.get("type") == "message"
    ]
    content = "\n".join(value for value in messages if isinstance(value, str))
    return {
        "content": content or None,
        "finish_reason": None,
        "served_instance_match": True,
        "response_instance_id_sha256": served_hash,
    }


def _read_attempts(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _aggregate(attempts: list[dict[str, Any]], planned: int) -> dict[str, Any]:
    completed = [item for item in attempts if item.get("status") == "completed"]
    scorable = [item for item in completed if item.get("score", {}).get("scorable")]
    scores = [bool(item["score"]["score"]) for item in scorable]
    failures: dict[str, int] = {}
    for item in completed:
        failure = item.get("score", {}).get("failure_type")
        if failure:
            failures[failure] = failures.get(failure, 0) + 1
    deployment_success = [item.get("score", {}).get("score") == 1 for item in completed]
    return {
        "planned": planned,
        "attempted": len(attempts),
        "completed": len(completed),
        "scorable": len(scorable),
        "failed": sum(1 for item in completed if item.get("score", {}).get("score") != 1),
        "censored": sum(
            1
            for item in completed
            if item.get("score", {}).get("failure_type") == "output_budget_exhaustion"
        ),
        "unattempted": max(0, planned - len(attempts)),
        "capability_conditional_on_valid_execution": summarize_binary(scores),
        "end_to_end_deployment_success": summarize_binary(deployment_success)
        if completed
        else summarize_binary([]),
        "failure_types": failures,
    }


def _run_settings(
    plan: dict[str, Any],
    config: dict[str, Any],
    profile: dict[str, Any],
    override: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    requested = dict(config.get("operation_requested") or {})
    output_limit = profile.get("max_output_tokens", requested.get("max_output_tokens", 512))
    if not isinstance(output_limit, int) or output_limit <= 0:
        raise RunError("suite max_output_tokens must be a positive integer")
    requested["max_output_tokens"] = output_limit
    if override:
        requested.update(override)
    transmitted = dict(requested)
    transmitted.pop("max_tokens", None)
    transmitted["reasoning"] = plan["reasoning_evidence"]["transmitted"]
    return requested, transmitted


def _run_fingerprint(
    plan: dict[str, Any], config: dict[str, Any], requested: dict[str, Any]
) -> str:
    return _sha256_json(
        {
            "config": config,
            "model": plan["model_id"],
            "locality": plan["locality_evidence"],
            "model_instance": plan["model_instance_evidence"],
            "protocol": plan["protocol"],
            "settings": requested,
        }
    )


def _initial_run_manifest(
    plan: dict[str, Any],
    suite: str,
    tasks: list[Task],
    run_id: str,
    fingerprint: str,
    requested: dict[str, Any],
    transmitted: dict[str, Any],
) -> dict[str, Any]:
    return {
        **plan,
        "run_id": run_id,
        "status": "running",
        "created_at": datetime.now(UTC).isoformat(),
        "fingerprint": fingerprint,
        "requested_settings": requested,
        "transmitted_settings": {
            **{key: value for key, value in transmitted.items() if value is not None},
            "store": False,
            "stream": False,
        },
        "effective_settings_status": "pending_response",
        "served_model_evidence": {
            "status": "not_verified",
            "requested_instance_id_sha256": _sha256_text(str(plan["model_id"])),
            "response_instance_id_sha256": None,
            "match": False,
        },
        "transport": {
            "endpoint": "/api/v1/chat",
            "retries": 0,
            "redirects": False,
            "cloud_fallback": False,
        },
        "calibration_heldout_separation": (
            "post_hoc_exploratory"
            if suite == "pilot"
            else "calibration"
            if suite == "calibration"
            else "harness"
        ),
        "private_task_manifest": [task.private_manifest() for task in tasks],
        "raw_evidence_publication_eligible": False,
    }


def _native_score(
    task: Task, payload: dict[str, Any], expected_instance: str, output_limit: int
) -> tuple[dict[str, Any], dict[str, Any], int, str]:
    view = _response_view(payload, expected_instance)
    protocol_error = view.get("protocol_error")
    score = (
        {"scorable": False, "score": None, "failure_type": protocol_error}
        if protocol_error
        else parse_and_score(task, view)
    )
    stats = payload.get("stats")
    completion = stats.get("total_output_tokens") if isinstance(stats, dict) else None
    if isinstance(completion, int):
        return view, score, completion, "response_usage"
    return view, score, output_limit, "requested_cap_fallback"


def _finalize_run_manifest(
    manifest: dict[str, Any],
    attempts: list[dict[str, Any]],
    planned: int,
    partial_reason: str | None,
    model_id: str,
) -> None:
    manifest["status"] = (
        "partial" if partial_reason is not None or len(attempts) < planned else "completed"
    )
    manifest["partial_reason"] = partial_reason
    manifest["completed_at"] = datetime.now(UTC).isoformat()
    manifest["aggregate"] = _aggregate(attempts, planned)
    instance_hash = _sha256_text(model_id)
    manifest["served_model_evidence"] = {
        "status": "not_verified",
        "requested_instance_id_sha256": instance_hash,
        "response_instance_id_sha256": None,
        "match": False,
    }
    manifest["reasoning_evidence"]["effective_status"] = "not_verified"
    manifest["effective_settings_status"] = "not_verified"
    if not attempts or not all(item.get("served_instance_match") is True for item in attempts):
        return
    response_hashes = {
        item.get("response_instance_id_sha256")
        for item in attempts
        if isinstance(item.get("response_instance_id_sha256"), str)
    }
    if response_hashes != {instance_hash}:
        return
    manifest["served_model_evidence"] = {
        "status": "verified",
        "requested_instance_id_sha256": instance_hash,
        "response_instance_id_sha256": response_hashes.pop(),
        "match": True,
    }
    manifest["reasoning_evidence"]["effective_status"] = "accepted_by_runtime"
    manifest["effective_settings_status"] = "accepted_by_runtime_not_read_back"


def _served_instance_failure(record: dict[str, Any]) -> bool:
    return record.get("score", {}).get("failure_type") in {
        "missing_served_model_instance",
        "served_model_instance_mismatch",
    }


def execute_run(
    suite: str,
    config_name: str,
    *,
    allow_expanded: bool = False,
    dry_run: bool = False,
    root: Path | None = None,
    operation_override: dict[str, Any] | None = None,
    run_label: str | None = None,
) -> dict[str, Any]:
    repo = root or project_root()
    plan = build_execution_plan(suite, config_name, allow_expanded=allow_expanded, root=repo)
    if dry_run:
        return {"status": "dry_run", "plan": plan}
    if plan["blockers"]:
        raise RunError("run blocked: " + "; ".join(plan["blockers"]))
    state = get_state_dir(repo)
    config = load_config(config_name, repo)
    profile = load_suite(suite, repo)
    if suite == "core":
        result = execute_evalscope_core(
            profile,
            repo=repo,
            state=state,
            server_origin=_config_openai_base_url(config),
        )
        return {
            **result,
            "runner": plan["runner"],
            "experiment_id": plan.get("experiment_id"),
            "model_is_splash": plan.get("model_is_splash", False),
            "locality_evidence": plan.get("locality_evidence", {}),
            "model_instance_evidence": plan.get("model_instance_evidence", {}),
        }
    tasks = suite_tasks(suite)
    requested_settings, settings = _run_settings(plan, config, profile, operation_override)
    fingerprint = _run_fingerprint(plan, config, requested_settings)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    label = f"-{run_label}" if run_label else ""
    run_id = f"{plan['experiment_id']}-{timestamp}{label}"
    run_dir = state / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    manifest = _initial_run_manifest(
        plan, suite, tasks, run_id, fingerprint, requested_settings, settings
    )
    manifest_path = run_dir / "manifest.json"
    attempts_path = run_dir / "attempts.jsonl"
    _write_json(manifest_path, manifest)
    origin = _config_origin(config)
    api_key = _api_key(config)
    partial_reason: str | None = None
    for task in tasks:
        try:
            with _locked_session_ledger(state, plan["limits"]) as (ledger_path, ledger):
                _check_budget(ledger, int(settings["max_output_tokens"]))
                attempt_started = datetime.now(UTC).isoformat()
                try:
                    payload, elapsed = _chat_once(
                        origin,
                        api_key,
                        str(plan["model_id"]),
                        task.prompt,
                        settings,
                    )
                    view, score, completion_tokens, usage_provenance = _native_score(
                        task,
                        payload,
                        str(plan["model_id"]),
                        int(settings["max_output_tokens"]),
                    )
                    ledger["live_requests"] = int(ledger["live_requests"]) + 1
                    ledger["generated_tokens"] = int(ledger["generated_tokens"]) + completion_tokens
                    _write_json(ledger_path, ledger)
                    record = {
                        "attempt_index": len(_read_attempts(attempts_path)) + 1,
                        "task_id": task.task_id,
                        "base_task_id": task.base_task_id or task.task_id,
                        "family": task.family,
                        "status": "completed",
                        "started_at": attempt_started,
                        "completed_at": datetime.now(UTC).isoformat(),
                        "elapsed_seconds": elapsed,
                        "request_sha256": _sha256_json(
                            {
                                "model": plan["model_id"],
                                "prompt": task.prompt,
                                "settings": settings,
                            }
                        ),
                        "response_sha256": _sha256_json(payload),
                        "raw_response": payload,
                        "score": score,
                        "finish_reason": view.get("finish_reason"),
                        "completion_tokens": completion_tokens,
                        "token_usage_provenance": usage_provenance,
                        "retries": 0,
                        "served_instance_match": view.get("served_instance_match", False),
                        "response_instance_id_sha256": view.get("response_instance_id_sha256"),
                    }
                except RunError as error:
                    ledger["live_requests"] = int(ledger["live_requests"]) + 1
                    _write_json(ledger_path, ledger)
                    record = {
                        "attempt_index": len(_read_attempts(attempts_path)) + 1,
                        "task_id": task.task_id,
                        "base_task_id": task.base_task_id or task.task_id,
                        "family": task.family,
                        "status": "completed",
                        "started_at": attempt_started,
                        "completed_at": datetime.now(UTC).isoformat(),
                        "raw_response": None,
                        "score": {
                            "scorable": False,
                            "score": None,
                            "failure_type": "server_http_or_transport_error",
                        },
                        "error_class": type(error).__name__,
                        "retries": 0,
                    }
        except BudgetExceeded as error:
            partial_reason = str(error)
            break
        _append_jsonl(attempts_path, record)
        if _served_instance_failure(record):
            partial_reason = (
                "LM Studio did not prove the selected model instance served the response."
            )
            break
    attempts = _read_attempts(attempts_path)
    _finalize_run_manifest(manifest, attempts, len(tasks), partial_reason, str(plan["model_id"]))
    _write_json(manifest_path, manifest)
    return {"status": manifest["status"], "run_id": run_id, "manifest": manifest}


def _run_dir(run_id: str, root: Path | None = None) -> Path:
    if run_id != Path(run_id).name or any(value in run_id for value in ("/", "\\", "..")):
        raise RunError("run ID must be a simple identifier")
    path = get_state_dir(root or project_root()) / "runs" / run_id
    if not path.is_dir():
        raise RunError(f"run not found: {run_id}")
    return path


def load_run(run_id: str, root: Path | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    directory = _run_dir(run_id, root)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    return manifest, _read_attempts(directory / "attempts.jsonl")


def _refuse_evalscope_operation(manifest: dict[str, Any], error: RunError) -> None:
    if manifest.get("suite") == "core" or manifest.get("runner") == "evalscope-1.12":
        raise error


def resume_run(run_id: str, *, dry_run: bool = False, root: Path | None = None) -> dict[str, Any]:
    repo = root or project_root()
    directory = _run_dir(run_id, repo)
    manifest, attempts = load_run(run_id, repo)
    _refuse_evalscope_operation(
        manifest,
        ResumeRefused(
            "EvalScope core resume is unavailable; rerun the unchanged frozen manifest set."
        ),
    )
    if manifest["status"] == "completed":
        return {"status": "already_complete", "run_id": run_id}
    if any(_served_instance_failure(record) for record in attempts):
        raise ResumeRefused("resume refused after unverified served model instance evidence")
    config = load_config(manifest["config_id"], repo)
    current_plan = build_plan(
        manifest["suite"],
        manifest["config_id"],
        allow_expanded=manifest.get("allow_expanded", False),
        root=repo,
    )
    current_fingerprint = _sha256_json(
        {
            "config": config,
            "model": current_plan["model_id"],
            "locality": current_plan["locality_evidence"],
            "model_instance": current_plan["model_instance_evidence"],
            "protocol": current_plan["protocol"],
            "settings": manifest["requested_settings"],
        }
    )
    if current_fingerprint != manifest["fingerprint"]:
        raise ResumeRefused(
            "resume fingerprint mismatch: model, settings, scorer, budget, or task protocol changed"
        )
    completed_ids = {item["task_id"] for item in attempts}
    remaining = [
        item for item in manifest["private_task_manifest"] if item["task_id"] not in completed_ids
    ]
    if dry_run:
        return {
            "status": "dry_run",
            "run_id": run_id,
            "remaining_count": len(remaining),
            "fingerprint_verified": True,
        }
    state = get_state_dir(repo)
    settings = manifest["transmitted_settings"]
    partial_reason: str | None = None
    for item in remaining:
        task = Task(
            item["task_id"],
            item["family"],
            item["prompt"],
            item["scorer"],
            item["expected"],
            item.get("base_task_id"),
        )
        try:
            with _locked_session_ledger(state, manifest["limits"]) as (
                ledger_path,
                ledger,
            ):
                _check_budget(ledger, int(settings["max_output_tokens"]))
                try:
                    payload, elapsed = _chat_once(
                        _config_origin(config),
                        _api_key(config),
                        str(manifest["model_id"]),
                        task.prompt,
                        settings,
                    )
                    view, score, completion_tokens, usage_provenance = _native_score(
                        task,
                        payload,
                        str(manifest["model_id"]),
                        int(settings["max_output_tokens"]),
                    )
                    record = {
                        "attempt_index": len(attempts) + 1,
                        "task_id": task.task_id,
                        "base_task_id": task.base_task_id or task.task_id,
                        "family": task.family,
                        "status": "completed",
                        "started_at": datetime.now(UTC).isoformat(),
                        "completed_at": datetime.now(UTC).isoformat(),
                        "elapsed_seconds": elapsed,
                        "request_sha256": _sha256_json(
                            {
                                "model": manifest["model_id"],
                                "prompt": task.prompt,
                                "settings": settings,
                            }
                        ),
                        "response_sha256": _sha256_json(payload),
                        "raw_response": payload,
                        "score": score,
                        "finish_reason": view.get("finish_reason"),
                        "completion_tokens": completion_tokens,
                        "token_usage_provenance": usage_provenance,
                        "retries": 0,
                        "resumed": True,
                        "served_instance_match": view.get("served_instance_match", False),
                        "response_instance_id_sha256": view.get("response_instance_id_sha256"),
                    }
                except RunError as error:
                    completion_tokens = 0
                    record = {
                        "attempt_index": len(attempts) + 1,
                        "task_id": task.task_id,
                        "base_task_id": task.base_task_id or task.task_id,
                        "family": task.family,
                        "status": "completed",
                        "score": {
                            "scorable": False,
                            "score": None,
                            "failure_type": "server_http_or_transport_error",
                        },
                        "error_class": type(error).__name__,
                        "retries": 0,
                        "resumed": True,
                    }
                ledger["live_requests"] = int(ledger["live_requests"]) + 1
                ledger["generated_tokens"] = int(ledger["generated_tokens"]) + completion_tokens
                _write_json(ledger_path, ledger)
        except BudgetExceeded as error:
            partial_reason = str(error)
            break
        attempts.append(record)
        _append_jsonl(directory / "attempts.jsonl", record)
        if _served_instance_failure(record):
            partial_reason = (
                "LM Studio did not prove the selected model instance served the response."
            )
            break
    _finalize_run_manifest(
        manifest,
        attempts,
        len(manifest["private_task_manifest"]),
        partial_reason,
        str(manifest["model_id"]),
    )
    manifest["last_resumed_at"] = datetime.now(UTC).isoformat()
    _write_json(directory / "manifest.json", manifest)
    return {
        "status": manifest["status"],
        "run_id": run_id,
        "remaining_count": manifest["aggregate"]["unattempted"],
    }


def rescore_run(run_id: str, scorer_version: str, *, root: Path | None = None) -> dict[str, Any]:
    repo = root or project_root()
    directory = _run_dir(run_id, repo)
    manifest, attempts = load_run(run_id, repo)
    _refuse_evalscope_operation(
        manifest,
        RunError(
            "EvalScope core rescore is unavailable; family scorers are pinned in the private "
            "frozen manifests."
        ),
    )
    if scorer_version not in {"builtin-exact-v1"}:
        raise RunError("requested scorer version is not installed")
    tasks = {item["task_id"]: item for item in manifest["private_task_manifest"]}
    rescored: list[dict[str, Any]] = []
    for attempt in attempts:
        task_data = tasks[attempt["task_id"]]
        task = Task(
            task_data["task_id"],
            task_data["family"],
            task_data["prompt"],
            task_data["scorer"],
            task_data["expected"],
            task_data.get("base_task_id"),
        )
        raw = attempt.get("raw_response")
        if not isinstance(raw, dict):
            score = attempt["score"]
        else:
            view = _response_view(raw, str(manifest["model_id"]))
            protocol_error = view.get("protocol_error")
            score = (
                {"scorable": False, "score": None, "failure_type": protocol_error}
                if protocol_error
                else parse_and_score(task, view)
            )
        rescored.append(
            {
                "task_id": task.task_id,
                "score": score,
                "source_attempt_index": attempt["attempt_index"],
            }
        )
    derived_id = f"{run_id}-rescore-{scorer_version}-{_sha256_json(rescored)[:8]}"
    output = {
        "schema_version": 1,
        "derived_result_id": derived_id,
        "source_run_id": run_id,
        "scorer_version": scorer_version,
        "created_at": datetime.now(UTC).isoformat(),
        "new_inference_requests": 0,
        "results": rescored,
        "aggregate": _aggregate(
            [{"status": "completed", "score": item["score"]} for item in rescored], len(rescored)
        ),
    }
    _write_json(directory / "derived" / f"{derived_id}.json", output)
    return output


def compare_runs(run_ids: list[str], *, root: Path | None = None) -> dict[str, Any]:
    if len(run_ids) != 2:
        raise RunError("compare currently requires exactly two run IDs")
    left_manifest, left_attempts = load_run(run_ids[0], root)
    right_manifest, right_attempts = load_run(run_ids[1], root)
    protocol_fields = ("suite", "selection_hash")
    mismatches = [
        field for field in protocol_fields if left_manifest.get(field) != right_manifest.get(field)
    ]
    if left_manifest.get("protocol", {}).get("scorer_version") != right_manifest.get(
        "protocol", {}
    ).get("scorer_version"):
        mismatches.append("scorer_version")
    if mismatches:
        raise RunError("comparison refused: unmatched protocol fields: " + ", ".join(mismatches))
    left_scores = {
        item["task_id"]: bool(item["score"]["score"])
        for item in left_attempts
        if item.get("score", {}).get("scorable")
    }
    right_scores = {
        item["task_id"]: bool(item["score"]["score"])
        for item in right_attempts
        if item.get("score", {}).get("scorable")
    }
    paired = paired_binary_difference(left_scores, right_scores)
    return {
        "schema_version": 1,
        "comparison_type": "paired_local_runs",
        "left_run": run_ids[0],
        "right_run": run_ids[1],
        "protocol_matched": True,
        "paired": paired,
        "left_aggregate": left_manifest.get("aggregate"),
        "right_aggregate": right_manifest.get("aggregate"),
        "caution": "A small or non-significant difference does not establish equivalence.",
    }
