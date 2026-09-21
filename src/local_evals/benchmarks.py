"""Fail-closed private core-benchmark qualification and EvalScope launching."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit, urlunsplit

from . import evalscope_exact
from .config import ConfigurationError, ExternalStatePaths, secure_resolve
from .sandbox import SandboxError, SandboxPolicy, validate_attestation

__all__ = ["CoreBenchmarkError", "execute_evalscope_core", "inspect_core_readiness"]

EVALSCOPE_VERSION = "1.12.0"
MODEL_ALIAS = "racecraft-splash-local"
OPENAI_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})
FAMILY_REQUIREMENTS: tuple[tuple[str, int], ...] = (
    ("gpqa_diamond", 12),
    ("ifeval", 16),
    ("mmlu_pro", 14),
    ("tool_json", 10),
    ("context", 8),
)
SESSION_GENERATED_TOKEN_CAP = 250_000
MMLU_PRO_SUBSETS = (
    "computer science",
    "math",
    "chemistry",
    "engineering",
    "law",
    "biology",
    "health",
    "physics",
    "business",
    "philosophy",
    "economics",
    "other",
    "psychology",
    "history",
)
_EVALSCOPE_CONTRACTS: dict[str, tuple[str, str, tuple[str, ...], int, bool]] = {
    "gpqa_diamond": ("gpqa_diamond", "train", ("default",), 0, False),
    "ifeval": ("ifeval", "train", ("default",), 0, False),
    "mmlu_pro": ("mmlu_pro", "test", MMLU_PRO_SUBSETS, 0, False),
    "tool_json": (evalscope_exact.TOOL_BENCHMARK, "test", ("tool_json",), 0, False),
    "context": (evalscope_exact.CONTEXT_BENCHMARK, "test", ("context",), 0, False),
}
_CUSTOM_FAMILIES = {"tool_json", "context"}
_CUSTOM_ADAPTER_CONTRACTS = {
    "tool_json": (evalscope_exact.TOOL_BENCHMARK, evalscope_exact.TOOL_SCORER_REVISION),
    "context": (evalscope_exact.CONTEXT_BENCHMARK, evalscope_exact.CONTEXT_SCORER_REVISION),
}
_SHA256_RE = frozenset("0123456789abcdef")
_FAMILY_MANIFEST_KEYS = {
    "schema_version",
    "family",
    "sample_count",
    "dataset_id",
    "evidence_split",
    "evalscope_dataset",
    "evalscope_split",
    "evalscope_subsets",
    "few_shot_num",
    "few_shot_random",
    "prompt_template_revision",
    "scorer_revision",
    "metric_variant",
    "dataset_revision",
    "dataset_path",
    "dataset_index_path",
    "dataset_tree_sha256",
    "ordered_sample_ids",
    "ordered_sample_ids_sha256",
    "frozen_before_tuning",
    "contamination_review_revision",
    "repeats",
    "all_attempts_in_denominator",
    "license_authorized",
    "license_authorization_revision",
    "calibration_ids",
    "heldout_ids",
}
_CUSTOM_PROVENANCE_KEYS = {
    "adapter_id",
    "adapter_revision",
    "adapter_source_sha256",
}
_PROFILE_FAMILY_KEYS = (
    "sample_count",
    "dataset_id",
    "evidence_split",
    "evalscope_dataset",
    "evalscope_split",
    "evalscope_subsets",
    "few_shot_num",
    "few_shot_random",
    "prompt_template_revision",
    "scorer_revision",
    "metric_variant",
)
_DATASET_INDEX_KEYS = {
    "schema_version",
    "family",
    "evidence_split",
    "evalscope_split",
    "evalscope_subsets",
    "record_count",
    "records_path",
    "records_sha256",
    "sample_id_field",
    "subset_field",
    "ordered_sample_ids",
    "ordered_subsets",
}


class CoreBenchmarkError(ValueError):
    """Raised when private core evidence or launch configuration is not qualified."""


class _Runner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]: ...


@dataclass(frozen=True)
class _Family:
    name: str
    sample_count: int
    evalscope_dataset: str
    evidence_split: str
    evalscope_split: str
    evalscope_subsets: tuple[str, ...]
    few_shot_num: int
    few_shot_random: bool
    dataset_path: Path
    dataset_revision: str
    ordered_sample_ids: tuple[str, ...]
    prompt_template_revision: str
    scorer_revision: str
    metric_variant: str
    manifest_sha256: str
    ordered_sample_ids_sha256: str
    dataset_tree_sha256: str
    dataset_index_sha256: str
    adapter_id: str | None
    adapter_revision: str | None
    adapter_source_sha256: str | None


@dataclass(frozen=True)
class _Prepared:
    state: Path
    executable: str
    api_url: str
    manifest_set_sha256: str
    families: tuple[_Family, ...]
    coding_requested: bool
    max_output_tokens: int
    session_generated_token_cap: int
    blockers: tuple[str, ...]


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _SHA256_RE for character in value)
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CoreBenchmarkError(f"{label} must be a mapping")
    return cast(Mapping[str, Any], value)


def _nonblank(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoreBenchmarkError(f"{label} must be nonblank")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    missing = expected - set(value)
    extra = set(value) - expected
    if missing or extra:
        raise CoreBenchmarkError(f"{label} has missing or unsupported fields")


def _read_json(path: Path, label: str) -> tuple[Mapping[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoreBenchmarkError(f"{label} is not readable canonical JSON") from exc
    return _mapping(value, label), raw


def _private_path(state: Path, value: Any, label: str, *, directory: bool = False) -> Path:
    relative = Path(_nonblank(value, label))
    if relative.is_absolute() or ".." in relative.parts:
        raise CoreBenchmarkError(f"{label} must be external-state-relative")
    try:
        path = secure_resolve(state / relative, must_exist=True)
    except ConfigurationError as exc:
        raise CoreBenchmarkError(f"{label} is unsafe or unavailable") from exc
    if not path.is_relative_to(state):
        raise CoreBenchmarkError(f"{label} escaped external state")
    expected_type = path.is_dir() if directory else path.is_file()
    if not expected_type:
        raise CoreBenchmarkError(f"{label} has the wrong filesystem type")
    return path


def _dataset_tree_sha256(root: Path, family: str) -> str:
    inventory: list[dict[str, str]] = []
    try:
        candidates = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
        for candidate in candidates:
            mode = candidate.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise CoreBenchmarkError(f"{family} dataset tree contains symbolic links")
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise CoreBenchmarkError(f"{family} dataset tree contains a non-regular entry")
            inventory.append(
                {
                    "path": candidate.relative_to(root).as_posix(),
                    "sha256": _sha256(candidate.read_bytes()),
                }
            )
    except OSError as exc:
        raise CoreBenchmarkError(f"{family} dataset tree is unreadable") from exc
    if not inventory:
        raise CoreBenchmarkError(f"{family} dataset tree is empty")
    encoded = json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    return _sha256(encoded)


def _string_ids(value: Any, label: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise CoreBenchmarkError(f"{label} must be a nonempty list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise CoreBenchmarkError(f"{label} must contain nonblank strings")
    result = tuple(cast(list[str], value))
    if len(result) != len(set(result)):
        raise CoreBenchmarkError(f"{label} must contain unique IDs")
    return result


def _string_values(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise CoreBenchmarkError(f"{label} must be a nonempty list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise CoreBenchmarkError(f"{label} must contain nonblank strings")
    return tuple(cast(list[str], value))


def _selection_sha256(sample_ids: Sequence[str]) -> str:
    encoded = json.dumps(list(sample_ids), separators=(",", ":")).encode()
    return _sha256(encoded)


def _adapter_source_sha256() -> str:
    source = getattr(evalscope_exact, "__file__", None)
    if not source:
        raise CoreBenchmarkError("exact adapter source location is unavailable")
    try:
        return _sha256(Path(source).read_bytes())
    except OSError as exc:
        raise CoreBenchmarkError("exact adapter source is unreadable") from exc


def _validate_custom_profile(family: str, config: Mapping[str, Any]) -> None:
    if family not in _CUSTOM_FAMILIES:
        return
    expected_adapter, expected_scorer = _CUSTOM_ADAPTER_CONTRACTS[family]
    if config.get("adapter_id") != expected_adapter:
        raise CoreBenchmarkError(f"{family} adapter ID does not match the qualified contract")
    if config.get("adapter_revision") != evalscope_exact.ADAPTER_REVISION:
        raise CoreBenchmarkError(f"{family} adapter revision does not match the qualified contract")
    if config.get("scorer_revision") != expected_scorer:
        raise CoreBenchmarkError(f"{family} scorer revision does not match the qualified contract")


def _validate_profile(profile: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    exact = {
        "suite": "core",
        "runner": "evalscope-1.12",
        "evalscope_version": EVALSCOPE_VERSION,
        "model_alias": MODEL_ALIAS,
        "eval_type": "openai_api",
        "api_path": "/v1",
        "eval_batch_size": 1,
        "transport_retries": 0,
        "repetitions": 1,
        "cache_policy": "disabled",
        "judge_policy": "disabled",
        "failure_policy": "all_attempts_in_denominator",
        "inspect_fallback": "unavailable",
    }
    labels = {
        "suite": "suite",
        "runner": "runner",
        "evalscope_version": "EvalScope version",
        "model_alias": "model alias",
        "eval_type": "evaluation type",
        "api_path": "API path",
        "eval_batch_size": "batch size",
        "transport_retries": "transport retries",
        "repetitions": "repetitions",
        "cache_policy": "cache policy",
        "judge_policy": "judge policy",
        "failure_policy": "denominator policy",
        "inspect_fallback": "Inspect fallback",
    }
    for field, expected in exact.items():
        if profile.get(field) != expected:
            raise CoreBenchmarkError(f"{labels[field]} does not match the core contract")
    families = _mapping(profile.get("families"), "profile families")
    required = dict(FAMILY_REQUIREMENTS)
    if set(families) != set(required):
        raise CoreBenchmarkError("profile families must be exactly the five core families")
    validated: dict[str, Mapping[str, Any]] = {}
    for family, count in FAMILY_REQUIREMENTS:
        config = _mapping(families.get(family), f"{family} profile")
        if config.get("sample_count") != count:
            raise CoreBenchmarkError(f"{family} sample count must be {count}")
        for field in (
            "dataset_id",
            "evidence_split",
            "evalscope_dataset",
            "evalscope_split",
            "prompt_template_revision",
            "scorer_revision",
            "metric_variant",
        ):
            _nonblank(config.get(field), f"{family} {field}")
        if config.get("evidence_split") != "held_out":
            raise CoreBenchmarkError(f"{family} evidence split must be held_out")
        subsets = _string_ids(config.get("evalscope_subsets"), f"{family} EvalScope subsets")
        expected_dataset, expected_split, expected_subsets, expected_shots, expected_random = (
            _EVALSCOPE_CONTRACTS[family]
        )
        if (
            config.get("evalscope_dataset") != expected_dataset
            or config.get("evalscope_split") != expected_split
            or subsets != expected_subsets
            or config.get("few_shot_num") != expected_shots
            or config.get("few_shot_random") is not expected_random
        ):
            raise CoreBenchmarkError(
                f"{family} EvalScope adapter contract does not match version 1.12.0"
            )
        _validate_custom_profile(family, config)
        validated[family] = config
    max_output_tokens = profile.get("max_output_tokens")
    session_cap = profile.get("session_generated_token_cap")
    if (
        not isinstance(max_output_tokens, int)
        or isinstance(max_output_tokens, bool)
        or max_output_tokens < 1
    ):
        raise CoreBenchmarkError("max_output_tokens must be a positive integer")
    if session_cap != SESSION_GENERATED_TOKEN_CAP:
        raise CoreBenchmarkError("session generated-token cap must equal 250000")
    total_samples = sum(dict(FAMILY_REQUIREMENTS).values())
    if total_samples * max_output_tokens > session_cap:
        raise CoreBenchmarkError("core generation budget exceeds the 250000-token session cap")
    _nonblank(profile.get("manifest_set_path"), "manifest_set_path")
    expected_set_hash = profile.get("manifest_set_sha256")
    if expected_set_hash is not None and not _is_sha256(expected_set_hash):
        raise CoreBenchmarkError("manifest-set hash must be lowercase SHA-256")
    return validated


def _validate_server(server_origin: str) -> str:
    try:
        parsed = urlsplit(server_origin)
        port = parsed.port
    except ValueError as exc:
        raise CoreBenchmarkError("server origin is invalid") from exc
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "127.0.0.1",
        "::1",
        "localhost",
    }:
        raise CoreBenchmarkError("server origin must use a loopback HTTP endpoint")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CoreBenchmarkError("server origin must not contain credentials, query, or fragment")
    if parsed.path.rstrip("/") != "/v1":
        raise CoreBenchmarkError("server origin must end at the exact /v1 API root")
    host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parsed.scheme, netloc, "/v1", "", ""))


def _resolve_evalscope(executable: str | None, version: str | None) -> str:
    resolved_executable = executable or shutil.which("evalscope")
    if not resolved_executable:
        raise CoreBenchmarkError("EvalScope executable is unavailable")
    executable_path = Path(resolved_executable)
    if executable_path.parent != Path(".") and not executable_path.is_file():
        raise CoreBenchmarkError("EvalScope executable is unavailable")
    if version is None:
        try:
            version = importlib.metadata.version("evalscope")
        except importlib.metadata.PackageNotFoundError as exc:
            raise CoreBenchmarkError("EvalScope version is unavailable") from exc
    if version != EVALSCOPE_VERSION:
        raise CoreBenchmarkError(f"EvalScope version must be exactly {EVALSCOPE_VERSION}")
    return resolved_executable


def _index_records(
    family: str,
    count: int,
    manifest: Mapping[str, Any],
    state: Path,
    dataset_path: Path,
) -> tuple[str, tuple[str, ...], str]:
    index_path = _private_path(
        state,
        manifest.get("dataset_index_path"),
        f"{family} dataset index path",
    )
    index, index_raw = _read_json(index_path, f"{family} dataset index")
    _exact_keys(index, _DATASET_INDEX_KEYS, f"{family} dataset index")
    if index.get("schema_version") != 1 or index.get("family") != family:
        raise CoreBenchmarkError(f"{family} dataset index identity is invalid")
    for field in ("evidence_split", "evalscope_split", "evalscope_subsets"):
        if index.get(field) != manifest.get(field):
            raise CoreBenchmarkError(f"{family} dataset index {field} does not match")
    if index.get("record_count") != count:
        raise CoreBenchmarkError(f"{family} dataset index record count must be {count}")
    sample_id_field = _nonblank(index.get("sample_id_field"), f"{family} sample ID field")
    subset_field = _nonblank(index.get("subset_field"), f"{family} subset field")
    records_relative = Path(_nonblank(index.get("records_path"), f"{family} records path"))
    if records_relative.is_absolute() or ".." in records_relative.parts:
        raise CoreBenchmarkError(f"{family} records path must be dataset-relative")
    dataset_root = dataset_path if dataset_path.is_dir() else dataset_path.parent
    try:
        records_path = secure_resolve(dataset_root / records_relative, must_exist=True)
    except ConfigurationError as exc:
        raise CoreBenchmarkError(f"{family} records path is unsafe or unavailable") from exc
    if not records_path.is_file() or not records_path.is_relative_to(dataset_root):
        raise CoreBenchmarkError(f"{family} records path escaped the frozen dataset")
    if family in _CUSTOM_FAMILIES and records_path != dataset_path:
        raise CoreBenchmarkError(f"{family} dataset must be the indexed JSONL file")
    if family not in _CUSTOM_FAMILIES and records_relative != Path(
        f"{manifest['evalscope_split']}.jsonl"
    ):
        raise CoreBenchmarkError(f"{family} records file does not match the EvalScope split")
    actual_files = {
        candidate.resolve() for candidate in dataset_root.rglob("*") if candidate.is_file()
    }
    if actual_files != {records_path, index_path}:
        raise CoreBenchmarkError(
            f"{family} frozen dataset must contain only the indexed records and sidecar"
        )
    expected_records_hash = index.get("records_sha256")
    try:
        records_raw = records_path.read_bytes()
    except OSError as exc:
        raise CoreBenchmarkError(f"{family} records are unreadable") from exc
    if not _is_sha256(expected_records_hash) or _sha256(records_raw) != expected_records_hash:
        raise CoreBenchmarkError(f"{family} records digest does not match")
    expected_ids = _string_ids(index.get("ordered_sample_ids"), f"{family} indexed sample IDs")
    expected_subsets = _string_values(index.get("ordered_subsets"), f"{family} indexed subsets")
    if len(expected_ids) != count or len(expected_subsets) != count:
        raise CoreBenchmarkError(
            f"{family} dataset index count does not match the frozen selection"
        )
    actual_ids: list[str] = []
    actual_subsets: list[str] = []
    records: list[Mapping[str, Any]] = []
    try:
        for line in records_raw.decode("utf-8").splitlines():
            if not line.strip():
                continue
            record = _mapping(json.loads(line), f"{family} dataset record")
            records.append(record)
            actual_ids.append(_nonblank(record.get(sample_id_field), f"{family} record sample ID"))
            actual_subsets.append(_nonblank(record.get(subset_field), f"{family} record subset"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoreBenchmarkError(f"{family} records must be UTF-8 JSONL") from exc
    if tuple(actual_ids) != expected_ids:
        raise CoreBenchmarkError(f"{family} actual record IDs do not match the ordered manifest")
    if tuple(actual_subsets) != expected_subsets:
        raise CoreBenchmarkError(f"{family} actual record subsets do not match the dataset index")
    declared_subsets = tuple(cast(list[str], manifest["evalscope_subsets"]))
    if set(actual_subsets) != set(declared_subsets):
        raise CoreBenchmarkError(f"{family} actual record subsets do not match EvalScope subsets")
    if family == "mmlu_pro" and tuple(actual_subsets) != MMLU_PRO_SUBSETS:
        raise CoreBenchmarkError(
            "mmlu_pro must contain exactly one ordered record per subject subset"
        )
    if family in _CUSTOM_FAMILIES:
        try:
            for record in records:
                evalscope_exact.validate_private_record(family, record)
        except ValueError as exc:
            raise CoreBenchmarkError(f"{family} private scorer record is invalid") from exc
    return _sha256(index_raw), expected_ids, cast(str, expected_records_hash)


def _validate_family(
    family: str,
    count: int,
    expected: Mapping[str, Any],
    manifest: Mapping[str, Any],
    state: Path,
    manifest_sha256: str,
) -> _Family:
    expected_keys = (
        _FAMILY_MANIFEST_KEYS | _CUSTOM_PROVENANCE_KEYS
        if family in _CUSTOM_FAMILIES
        else _FAMILY_MANIFEST_KEYS
    )
    _exact_keys(manifest, expected_keys, f"{family} manifest")
    _validate_family_identity(family, count, expected, manifest)
    revision = manifest.get("dataset_revision")
    if not _is_sha256(revision):
        raise CoreBenchmarkError(f"{family} dataset revision must be immutable SHA-256")
    dataset_path = _private_path(
        state,
        manifest.get("dataset_path"),
        f"{family} dataset path",
        directory=family not in _CUSTOM_FAMILIES,
    )
    dataset_root = dataset_path if dataset_path.is_dir() else dataset_path.parent
    tree_hash = manifest.get("dataset_tree_sha256")
    if not _is_sha256(tree_hash) or _dataset_tree_sha256(dataset_root, family) != tree_hash:
        raise CoreBenchmarkError(f"{family} dataset tree digest does not match")
    ordered, selection_hash = _validate_family_selection(family, count, manifest)
    dataset_index_hash, indexed_ids, _ = _index_records(
        family, count, manifest, state, dataset_path
    )
    if indexed_ids != ordered:
        raise CoreBenchmarkError(f"{family} indexed records do not match the heldout manifest")
    _validate_family_policy(family, manifest)
    return _Family(
        name=family,
        sample_count=count,
        evalscope_dataset=cast(str, manifest["evalscope_dataset"]),
        evidence_split=cast(str, manifest["evidence_split"]),
        evalscope_split=cast(str, manifest["evalscope_split"]),
        evalscope_subsets=tuple(cast(list[str], manifest["evalscope_subsets"])),
        few_shot_num=cast(int, manifest["few_shot_num"]),
        few_shot_random=cast(bool, manifest["few_shot_random"]),
        dataset_path=dataset_path,
        dataset_revision=cast(str, revision),
        ordered_sample_ids=ordered,
        prompt_template_revision=cast(str, manifest["prompt_template_revision"]),
        scorer_revision=cast(str, manifest["scorer_revision"]),
        metric_variant=cast(str, manifest["metric_variant"]),
        manifest_sha256=manifest_sha256,
        ordered_sample_ids_sha256=selection_hash,
        dataset_tree_sha256=cast(str, tree_hash),
        dataset_index_sha256=dataset_index_hash,
        adapter_id=cast(str, manifest["adapter_id"]) if family in _CUSTOM_FAMILIES else None,
        adapter_revision=(
            cast(str, manifest["adapter_revision"]) if family in _CUSTOM_FAMILIES else None
        ),
        adapter_source_sha256=(
            cast(str, manifest["adapter_source_sha256"]) if family in _CUSTOM_FAMILIES else None
        ),
    )


def _validate_family_identity(
    family: str,
    count: int,
    expected: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    if manifest.get("schema_version") != 1 or manifest.get("family") != family:
        raise CoreBenchmarkError(f"{family} manifest identity is invalid")
    if manifest.get("sample_count") != count:
        raise CoreBenchmarkError(f"{family} sample count must be {count}")
    for field in _PROFILE_FAMILY_KEYS[1:]:
        if manifest.get(field) != expected.get(field):
            raise CoreBenchmarkError(f"{family} {field} does not match the frozen profile")
    if family in _CUSTOM_FAMILIES:
        for field in ("adapter_id", "adapter_revision"):
            if manifest.get(field) != expected.get(field):
                raise CoreBenchmarkError(f"{family} {field} does not match the frozen profile")
        source_hash = manifest.get("adapter_source_sha256")
        if not _is_sha256(source_hash) or source_hash != _adapter_source_sha256():
            raise CoreBenchmarkError(f"{family} adapter source digest does not match")


def _validate_family_selection(
    family: str, count: int, manifest: Mapping[str, Any]
) -> tuple[tuple[str, ...], str]:
    heldout = _string_ids(manifest.get("heldout_ids"), f"{family} heldout IDs")
    ordered = _string_ids(manifest.get("ordered_sample_ids"), f"{family} ordered sample IDs")
    if ordered != heldout or len(ordered) != count:
        raise CoreBenchmarkError(f"{family} ordered heldout sample count or order is invalid")
    selection_hash = manifest.get("ordered_sample_ids_sha256")
    if not _is_sha256(selection_hash) or _selection_sha256(ordered) != selection_hash:
        raise CoreBenchmarkError(f"{family} ordered sample hash does not match")
    calibration = _string_ids(manifest.get("calibration_ids"), f"{family} calibration IDs")
    if set(calibration) & set(heldout):
        raise CoreBenchmarkError(f"{family} calibration and heldout IDs must be disjoint")
    return ordered, cast(str, selection_hash)


def _validate_family_policy(family: str, manifest: Mapping[str, Any]) -> None:
    if manifest.get("frozen_before_tuning") is not True:
        raise CoreBenchmarkError(f"{family} selection was not frozen before tuning")
    _nonblank(manifest.get("contamination_review_revision"), f"{family} contamination revision")
    if manifest.get("repeats") != 1:
        raise CoreBenchmarkError(f"{family} repeats must equal 1")
    if manifest.get("all_attempts_in_denominator") is not True:
        raise CoreBenchmarkError(f"{family} denominator must include all attempts")
    if manifest.get("license_authorized") is not True:
        raise CoreBenchmarkError(f"{family} license authorization is missing")
    _nonblank(
        manifest.get("license_authorization_revision"),
        f"{family} license authorization revision",
    )


def _validated_families(
    profile: Mapping[str, Any],
    profile_families: Mapping[str, Mapping[str, Any]],
    state: Path,
) -> tuple[tuple[_Family, ...], str]:
    set_path = _private_path(state, profile.get("manifest_set_path"), "manifest-set path")
    manifest_set, raw = _read_json(set_path, "manifest set")
    set_digest = _sha256(raw)
    expected_digest = profile.get("manifest_set_sha256")
    if expected_digest is not None and expected_digest != set_digest:
        raise CoreBenchmarkError("manifest-set hash does not match")
    _exact_keys(manifest_set, {"schema_version", "families"}, "manifest set")
    if manifest_set.get("schema_version") != 1:
        raise CoreBenchmarkError("manifest-set schema version is unsupported")
    entries = _mapping(manifest_set.get("families"), "manifest-set families")
    if set(entries) != set(dict(FAMILY_REQUIREMENTS)):
        raise CoreBenchmarkError("manifest set must pin exactly the five core families")
    families: list[_Family] = []
    dataset_paths: set[Path] = set()
    for family, count in FAMILY_REQUIREMENTS:
        entry = _mapping(entries.get(family), f"{family} manifest-set entry")
        _exact_keys(entry, {"manifest_path", "manifest_sha256"}, f"{family} manifest-set entry")
        manifest_path = _private_path(state, entry.get("manifest_path"), f"{family} manifest path")
        manifest, family_raw = _read_json(manifest_path, f"{family} manifest")
        expected_hash = entry.get("manifest_sha256")
        if not _is_sha256(expected_hash) or _sha256(family_raw) != expected_hash:
            raise CoreBenchmarkError(f"{family} manifest hash does not match")
        validated = _validate_family(
            family,
            count,
            profile_families[family],
            manifest,
            state,
            cast(str, expected_hash),
        )
        if validated.dataset_path in dataset_paths:
            raise CoreBenchmarkError("core families must use distinct frozen dataset trees")
        dataset_paths.add(validated.dataset_path)
        families.append(validated)
    return tuple(families), set_digest


def _validate_coding(profile: Mapping[str, Any], state: Path) -> bool:
    coding_value = profile.get("coding")
    if coding_value is None:
        return False
    coding = _mapping(coding_value, "coding request")
    if coding.get("requested") is not True:
        return False
    sample_count = coding.get("sample_count")
    valid_count = (
        isinstance(sample_count, int)
        and not isinstance(sample_count, bool)
        and 1 <= sample_count <= 10
    )
    if not valid_count:
        raise CoreBenchmarkError("coding request must contain at most 10 samples")
    policy_path = _private_path(state, coding.get("sandbox_policy_path"), "sandbox policy path")
    policy_evidence, policy_raw = _read_json(policy_path, "sandbox policy")
    expected_policy_hash = coding.get("sandbox_policy_sha256")
    if not _is_sha256(expected_policy_hash) or _sha256(policy_raw) != expected_policy_hash:
        raise CoreBenchmarkError("sandbox policy hash does not match")
    try:
        policy = SandboxPolicy(**dict(policy_evidence))
    except (SandboxError, TypeError) as exc:
        raise CoreBenchmarkError("sandbox policy does not match the shared contract") from exc
    attestation_path = _private_path(
        state, coding.get("sandbox_attestation_path"), "sandbox attestation path"
    )
    attestation, raw = _read_json(attestation_path, "sandbox attestation")
    expected_hash = coding.get("sandbox_attestation_sha256")
    if not _is_sha256(expected_hash) or _sha256(raw) != expected_hash:
        raise CoreBenchmarkError("sandbox attestation hash does not match")
    try:
        validate_attestation(attestation, policy)
    except SandboxError as exc:
        raise CoreBenchmarkError("sandbox attestation does not match the shared contract") from exc
    return True


def _prepare(
    profile: Mapping[str, Any],
    *,
    repo: str | os.PathLike[str],
    state: str | os.PathLike[str],
    server_origin: str,
    evalscope_executable: str | None,
    evalscope_version: str | None,
) -> _Prepared:
    profile_families = _validate_profile(profile)
    try:
        paths = ExternalStatePaths.resolve(
            project_root=repo,
            state_dir=state,
            require_project=True,
            require_state=True,
        )
    except ConfigurationError as exc:
        raise CoreBenchmarkError("repository or external-state boundary is not qualified") from exc
    api_url = _validate_server(server_origin)
    executable = _resolve_evalscope(evalscope_executable, evalscope_version)
    families, set_digest = _validated_families(profile, profile_families, paths.state_dir)
    coding_requested = _validate_coding(profile, paths.state_dir)
    if coding_requested:
        raise CoreBenchmarkError(
            "coding execution is unavailable even after sandbox attestation; no fallback is allowed"
        )
    return _Prepared(
        state=paths.state_dir,
        executable=executable,
        api_url=api_url,
        manifest_set_sha256=set_digest,
        families=families,
        coding_requested=coding_requested,
        max_output_tokens=cast(int, profile["max_output_tokens"]),
        session_generated_token_cap=cast(int, profile["session_generated_token_cap"]),
        blockers=(),
    )


def _metadata(*, coding_requested: bool, prepared: _Prepared | None = None) -> dict[str, Any]:
    family_counts = dict(FAMILY_REQUIREMENTS)
    metadata: dict[str, Any] = {
        "suite": "core",
        "runner": "evalscope-1.12",
        "evalscope_version": EVALSCOPE_VERSION,
        "model_alias": MODEL_ALIAS,
        "eval_type": "openai_api",
        "max_output_tokens": prepared.max_output_tokens if prepared is not None else None,
        "session_generated_token_cap": (
            prepared.session_generated_token_cap if prepared is not None else None
        ),
        "families": family_counts,
        "total_samples": sum(family_counts.values()),
        "coding_requested": coding_requested,
    }
    if prepared is not None:
        metadata["manifest_set_sha256"] = prepared.manifest_set_sha256
        metadata["family_evidence"] = {
            family.name: {
                "manifest_sha256": family.manifest_sha256,
                "ordered_sample_ids_sha256": family.ordered_sample_ids_sha256,
                "dataset_tree_sha256": family.dataset_tree_sha256,
                "dataset_index_sha256": family.dataset_index_sha256,
                "evalscope_split": family.evalscope_split,
                "evalscope_subsets": list(family.evalscope_subsets),
                "few_shot_num": family.few_shot_num,
                "few_shot_random": family.few_shot_random,
                **(
                    {
                        "adapter_id": family.adapter_id,
                        "adapter_revision": family.adapter_revision,
                        "adapter_source_sha256": family.adapter_source_sha256,
                    }
                    if family.name in _CUSTOM_FAMILIES
                    else {}
                ),
            }
            for family in prepared.families
        }
    return metadata


def _call_arguments_valid(
    profile: object,
    repo: object,
    state: object,
    server_origin: object,
) -> Mapping[str, Any]:
    if not isinstance(profile, Mapping):
        raise CoreBenchmarkError("profile must be a mapping")
    if not isinstance(repo, (str, os.PathLike)) or not isinstance(state, (str, os.PathLike)):
        raise CoreBenchmarkError("repo and state must be filesystem paths")
    if not isinstance(server_origin, str):
        raise CoreBenchmarkError("server_origin must be a string")
    return cast(Mapping[str, Any], profile)


def inspect_core_readiness(
    profile: Mapping[str, Any],
    *,
    repo: str | os.PathLike[str],
    state: str | os.PathLike[str],
    server_origin: str,
    evalscope_executable: str | None = None,
    evalscope_version: str | None = None,
) -> dict[str, Any]:
    """Return sanitized readiness with hashes but without private paths or sample IDs."""

    checked_profile = _call_arguments_valid(profile, repo, state, server_origin)
    coding_value = checked_profile.get("coding")
    coding_requested = isinstance(coding_value, Mapping) and coding_value.get("requested") is True
    try:
        prepared = _prepare(
            checked_profile,
            repo=repo,
            state=state,
            server_origin=server_origin,
            evalscope_executable=evalscope_executable,
            evalscope_version=evalscope_version,
        )
    except CoreBenchmarkError as exc:
        return {
            "status": "blocked",
            "blockers": [str(exc)],
            "metadata": _metadata(coding_requested=coding_requested),
        }
    return {
        "status": "blocked" if prepared.blockers else "ready",
        "blockers": list(prepared.blockers),
        "metadata": _metadata(coding_requested=prepared.coding_requested, prepared=prepared),
    }


def _family_command(
    prepared: _Prepared, family: _Family, work_dir: Path, reasoning_mode: str
) -> list[str]:
    dataset_args = {
        family.evalscope_dataset: {
            "dataset_id": str(family.dataset_path),
            "subset_list": list(family.evalscope_subsets),
            "few_shot_num": family.few_shot_num,
            "few_shot_random": family.few_shot_random,
            "shuffle": False,
        }
    }
    dataset_dir = prepared.state / "evalscope" / "datasets"
    launcher = (
        [sys.executable, "-m", "local_evals.evalscope_exact", "eval"]
        if family.name in _CUSTOM_FAMILIES
        else [prepared.executable, "eval"]
    )
    return [
        *launcher,
        "--model",
        MODEL_ALIAS,
        "--model-id",
        MODEL_ALIAS,
        "--eval-type",
        "openai_api",
        "--api-url",
        prepared.api_url,
        "--api-key",
        "EMPTY",
        "--datasets",
        family.evalscope_dataset,
        "--dataset-args",
        json.dumps(dataset_args, sort_keys=True, separators=(",", ":")),
        "--dataset-dir",
        str(dataset_dir),
        "--eval-batch-size",
        "1",
        "--repeats",
        "1",
        "--generation-config",
        json.dumps(
            {
                "max_tokens": prepared.max_output_tokens,
                "reasoning_effort": reasoning_mode,
                "retries": 0,
                "stream": False,
            },
            separators=(",", ":"),
        ),
        "--enable-progress-tracker",
        "--work-dir",
        str(work_dir),
        "--no-timestamp",
    ]


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    path.chmod(0o700)


def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    path.chmod(0o600)


def _launch_environment(state: Path) -> dict[str, str]:
    environment = dict(os.environ)
    sensitive_suffixes = ("_API_KEY", "_ACCESS_TOKEN", "_AUTH_TOKEN")
    proxy_names = {"ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY"}
    for key in list(environment):
        normalized = key.upper()
        if normalized.endswith(sensitive_suffixes) or normalized in proxy_names:
            environment.pop(key, None)
    cache_root = state / "evalscope" / "cache"
    environment.update(
        {
            "EVALSCOPE_CACHE": str(cache_root / "evalscope"),
            "MODELSCOPE_CACHE": str(cache_root / "modelscope"),
            "HF_HOME": str(cache_root / "huggingface"),
            "HUGGINGFACE_HUB_CACHE": str(cache_root / "huggingface" / "hub"),
            "XDG_CACHE_HOME": str(cache_root / "xdg"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "MODELSCOPE_OFFLINE": "1",
            "NO_PROXY": "localhost,127.0.0.1,::1",
            "no_proxy": "localhost,127.0.0.1,::1",
        }
    )
    return environment


def _initialize_launch(prepared: _Prepared, reasoning_mode: str) -> tuple[Path, list[list[str]]]:
    if prepared.blockers:
        raise CoreBenchmarkError("; ".join(prepared.blockers))
    launch_root = prepared.state / "launches"
    launch_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    launch_root.chmod(0o700)
    launch_dir = launch_root / uuid.uuid4().hex
    _private_directory(launch_dir)
    work_root = launch_dir / "work"
    _private_directory(work_root)
    commands: list[list[str]] = []
    for family in prepared.families:
        family_work = work_root / family.name
        _private_directory(family_work)
        commands.append(_family_command(prepared, family, family_work, reasoning_mode))
    _write_private_json(
        launch_dir / "launch-evidence.json",
        {
            "schema_version": 1,
            "evalscope_version": EVALSCOPE_VERSION,
            "manifest_set_sha256": prepared.manifest_set_sha256,
            "reasoning_mode": reasoning_mode,
            "commands": commands,
            "status": "planned",
        },
    )
    return launch_dir, commands


def _execute_commands(
    prepared: _Prepared,
    launch_dir: Path,
    commands: Sequence[Sequence[str]],
    runner: Callable[..., subprocess.CompletedProcess[str]] | None,
) -> list[dict[str, Any]]:
    run = cast(_Runner, runner or subprocess.run)
    environment = _launch_environment(prepared.state)
    results: list[dict[str, Any]] = []
    family_results: list[dict[str, Any]] = []
    for family, command in zip(prepared.families, commands, strict=True):
        completed = run(
            command,
            cwd=prepared.state,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        results.append(
            {
                "family": family.name,
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
        _write_private_json(
            launch_dir / "results.json",
            {"schema_version": 1, "results": results},
        )
        if completed.returncode != 0:
            raise CoreBenchmarkError(f"EvalScope failed for {family.name}")
        family_results.append(_read_family_report(family, command))
    return family_results


def _report_metric_identity(family: _Family) -> dict[str, Any]:
    if family.name in {"gpqa_diamond", "mmlu_pro"}:
        return {"name": "accuracy", "aggregation": "mean", "dimensions": {}}
    if family.name == "ifeval":
        return {
            "name": "prompt_level_strict",
            "aggregation": "weighted_mean",
            "dimensions": {},
        }
    return {"name": "accuracy", "aggregation": "mean", "dimensions": {}}


def _report_path(command: Sequence[str], family: _Family) -> Path:
    try:
        work_dir = Path(command[command.index("--work-dir") + 1])
    except (ValueError, IndexError) as exc:
        raise CoreBenchmarkError(f"{family.name} aggregate report command is invalid") from exc
    return work_dir / "reports" / MODEL_ALIAS / f"{family.evalscope_dataset}.json"


def _exact_report_int(value: Any, expected: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == expected


def _read_family_report(family: _Family, command: Sequence[str]) -> dict[str, Any]:
    report_path = _report_path(command, family)
    try:
        if report_path.is_symlink() or not report_path.is_file():
            raise CoreBenchmarkError(f"{family.name} aggregate report is missing or unsafe")
        raw = report_path.read_bytes()
        if not raw or len(raw) > 1_048_576:
            raise CoreBenchmarkError(f"{family.name} aggregate report size is invalid")
        report = _mapping(json.loads(raw), f"{family.name} aggregate report document")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoreBenchmarkError(f"{family.name} aggregate report is unreadable") from exc

    expected_identity = _report_metric_identity(family)
    expected_metric = expected_identity["name"]
    primary = _mapping(
        report.get("primary_metric_identity"), f"{family.name} aggregate report identity"
    )
    metrics = report.get("metrics")
    if not isinstance(metrics, list):
        raise CoreBenchmarkError(f"{family.name} aggregate report metrics are invalid")
    matching = [
        _mapping(metric, f"{family.name} aggregate report metric")
        for metric in metrics
        if isinstance(metric, Mapping) and metric.get("identity") == expected_identity
    ]
    execution = _mapping(
        report.get("execution_summary"), f"{family.name} aggregate report execution"
    )
    valid_identity = dict(primary) == expected_identity
    valid_execution = (
        _exact_report_int(execution.get("requested"), family.sample_count)
        and _exact_report_int(execution.get("succeeded"), family.sample_count)
        and _exact_report_int(execution.get("errored"), 0)
        and execution.get("incomplete") is False
    )
    if (
        report.get("schema_version") != 2
        or report.get("dataset_name") != family.evalscope_dataset
        or report.get("model_name") != MODEL_ALIAS
        or report.get("primary_metric_unavailable_reason") is not None
        or not _exact_report_int(report.get("num"), family.sample_count)
        or not valid_identity
        or len(matching) != 1
        or not valid_execution
    ):
        raise CoreBenchmarkError(f"{family.name} aggregate report contract does not match")
    metric = matching[0]
    score = metric.get("score")
    if (
        not _exact_report_int(metric.get("num"), family.sample_count)
        or not isinstance(score, (int, float))
        or isinstance(score, bool)
        or not math.isfinite(score)
        or not 0 <= score <= 1
    ):
        raise CoreBenchmarkError(f"{family.name} aggregate report score is invalid")
    correct_value = float(score) * family.sample_count
    correct = round(correct_value)
    if not math.isclose(correct_value, correct, abs_tol=1e-9):
        raise CoreBenchmarkError(f"{family.name} aggregate report score is not an exact count")
    scorer_provenance = {
        "evalscope_version": EVALSCOPE_VERSION,
        "evalscope_dataset": family.evalscope_dataset,
        "scorer_revision": family.scorer_revision,
        "metric_variant": family.metric_variant,
        "adapter_revision": family.adapter_revision,
        "adapter_source_sha256": family.adapter_source_sha256,
    }
    return {
        "schema_version": 1,
        "family": family.name,
        "planned": family.sample_count,
        "attempted": family.sample_count,
        "scored": family.sample_count,
        "correct": correct,
        "score": float(score),
        "metric_name": expected_metric,
        "metric_unit": "proportion",
        "scorer_revision": family.scorer_revision,
        "scorer_provenance_sha256": _sha256(
            json.dumps(scorer_provenance, sort_keys=True, separators=(",", ":")).encode()
        ),
        "calibration_manifest_sha256": family.manifest_sha256,
        "selection_manifest_sha256": family.manifest_sha256,
        "ordered_sample_ids_sha256": family.ordered_sample_ids_sha256,
        "dataset_tree_sha256": family.dataset_tree_sha256,
        "dataset_index_sha256": family.dataset_index_sha256,
        "report_sha256": _sha256(raw),
        "failure_counts": {
            "incorrect": family.sample_count - correct,
            "execution_error": 0,
            "unscored": 0,
        },
    }


def execute_evalscope_core(
    profile: Mapping[str, Any],
    *,
    repo: str | os.PathLike[str],
    state: str | os.PathLike[str],
    server_origin: str,
    evalscope_executable: str | None = None,
    evalscope_version: str | None = None,
    reasoning_mode: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, Any]:
    """Run qualified core families sequentially and retain all evidence externally."""

    checked_profile = _call_arguments_valid(profile, repo, state, server_origin)
    if reasoning_mode not in OPENAI_REASONING_EFFORTS:
        raise CoreBenchmarkError(
            "core execution requires an explicit OpenAI-compatible reasoning effort"
        )
    prepared = _prepare(
        checked_profile,
        repo=repo,
        state=state,
        server_origin=server_origin,
        evalscope_executable=evalscope_executable,
        evalscope_version=evalscope_version,
    )
    if prepared.blockers:
        raise CoreBenchmarkError("; ".join(prepared.blockers))
    launch_dir, commands = _initialize_launch(prepared, reasoning_mode)
    family_results = _execute_commands(prepared, launch_dir, commands, runner)
    return {
        "status": "completed",
        "families_completed": [family.name for family in prepared.families],
        "family_count": len(prepared.families),
        "sample_count": sum(family.sample_count for family in prepared.families),
        "model_alias": MODEL_ALIAS,
        "reasoning_mode": reasoning_mode,
        "runtime_evidence": {
            "transport": "evalscope_openai_api",
            "endpoint": "/v1/chat/completions",
            "evalscope_version": EVALSCOPE_VERSION,
        },
        "family_results": family_results,
    }
