#!/usr/bin/env python3
"""Freeze the five advertised non-coding core families into private state.

This command is intentionally offline.  It reads already-present official
Arrow caches and private user-authored JSONL, selects deterministic held-out
and calibration IDs, and emits the exact sidecars consumed by
``local_evals.benchmarks``.  It never prints records, IDs, or filesystem paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import stat
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from local_evals import benchmarks, evalscope_exact  # noqa: E402
from local_evals.jsonutil import StrictJSONError, canonical_json_text  # noqa: E402


class FreezeError(ValueError):
    """Raised when private inputs cannot be frozen without guessing."""


@dataclass(frozen=True)
class FamilyContract:
    count: int
    dataset_id: str
    evalscope_dataset: str
    split: str
    subsets: tuple[str, ...]
    prompt_revision: str
    scorer_revision: str
    metric_variant: str
    id_fields: tuple[str, ...]


_MMLU_SUBSETS = tuple(benchmarks.MMLU_PRO_SUBSETS)
_CONTRACTS: dict[str, FamilyContract] = {
    "gpqa_diamond": FamilyContract(
        12,
        "gpqa_diamond",
        "gpqa_diamond",
        "train",
        ("default",),
        "gpqa-diamond-zero-shot-v1",
        "gpqa-diamond-exact-choice-v1",
        "accuracy",
        ("sample_id", "Record ID", "record_id", "id", "key", "question_id"),
    ),
    "ifeval": FamilyContract(
        16,
        "ifeval",
        "ifeval",
        "train",
        ("default",),
        "ifeval-official-v1",
        "ifeval-strict-v1",
        "prompt_level_strict",
        ("sample_id", "key", "id", "question_id"),
    ),
    "mmlu_pro": FamilyContract(
        14,
        "mmlu_pro",
        "mmlu_pro",
        "test",
        _MMLU_SUBSETS,
        "mmlu-pro-zero-shot-v1",
        "mmlu-pro-exact-choice-v1",
        "accuracy",
        ("sample_id", "question_id", "id", "key"),
    ),
    "tool_json": FamilyContract(
        10,
        "tool_json",
        evalscope_exact.TOOL_BENCHMARK,
        "test",
        ("tool_json",),
        "tool-json-v1",
        evalscope_exact.TOOL_SCORER_REVISION,
        "schema_exact_accuracy",
        ("sample_id",),
    ),
    "context": FamilyContract(
        8,
        "context",
        evalscope_exact.CONTEXT_BENCHMARK,
        "test",
        ("context",),
        "context-retrieval-v1",
        evalscope_exact.CONTEXT_SCORER_REVISION,
        "accuracy",
        ("sample_id",),
    ),
}
_FAMILY_ORDER = tuple(name for name, _count in benchmarks.FAMILY_REQUIREMENTS)
_CUSTOM_FAMILIES = frozenset({"tool_json", "context"})
_OFFICIAL_FAMILIES = frozenset(set(_FAMILY_ORDER) - _CUSTOM_FAMILIES)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: object) -> bytes:
    try:
        return (canonical_json_text(value, ensure_ascii=False) + "\n").encode("utf-8")
    except StrictJSONError as exc:
        raise FreezeError("source records contain unsupported JSON values") from exc


def _selection_sha256(sample_ids: Sequence[str]) -> str:
    return _sha256(json.dumps(list(sample_ids), separators=(",", ":")).encode())


def _tree_sha256(root: Path) -> str:
    inventory: list[dict[str, str]] = []
    for candidate in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        mode = candidate.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise FreezeError("a dataset tree contains a symbolic link")
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise FreezeError("a dataset tree contains a non-regular entry")
        inventory.append(
            {
                "path": candidate.relative_to(root).as_posix(),
                "sha256": _sha256(candidate.read_bytes()),
            }
        )
    if not inventory:
        raise FreezeError("a frozen dataset tree is empty")
    return _sha256(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode())


def _git_ancestor(path: Path) -> bool:
    current = path if path.is_dir() else path.parent
    while True:
        if (current / ".git").exists() or (current / ".git").is_symlink():
            return True
        if current.parent == current:
            return False
        current = current.parent


def _secure_existing(path: Path, *, expected_directory: bool | None = None) -> Path:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise FreezeError("a required private input is unavailable") from exc
    if stat.S_ISLNK(mode):
        raise FreezeError("symbolic-link inputs are refused")
    if expected_directory is True and not stat.S_ISDIR(mode):
        raise FreezeError("the private output root must be a directory")
    if expected_directory is False and not stat.S_ISREG(mode):
        raise FreezeError("a private source must be a regular file")
    resolved = path.resolve(strict=True)
    if _git_ancestor(resolved):
        raise FreezeError("source and output paths must remain outside Git")
    return resolved


def _source_files(source: Path, *, arrow: bool) -> tuple[Path, ...]:
    source = _secure_existing(source)
    if source.is_file():
        expected_suffix = ".arrow" if arrow else ".jsonl"
        if source.suffix.lower() != expected_suffix:
            raise FreezeError("a source file has the wrong format")
        return (source,)
    if not source.is_dir():
        raise FreezeError("a source must be a regular file or directory")
    suffix = ".arrow" if arrow else ".jsonl"
    files: list[Path] = []
    for candidate in sorted(
        source.rglob("*"), key=lambda item: item.relative_to(source).as_posix()
    ):
        mode = candidate.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise FreezeError("source trees containing symbolic links are refused")
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise FreezeError("source trees must contain only regular files")
        if candidate.suffix.lower() == suffix:
            files.append(candidate)
    if not files:
        raise FreezeError("a source tree contains no supported records")
    return tuple(files)


def _source_revision(source: Path, files: Sequence[Path]) -> str:
    root = source if source.is_dir() else source.parent
    inventory = [
        {
            "path": file.relative_to(root).as_posix(),
            "sha256": _sha256(file.read_bytes()),
        }
        for file in files
    ]
    return _sha256(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode())


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FreezeError("source records contain non-finite numbers")
        return value
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise FreezeError("source record keys must be strings")
            result[key] = _json_safe(item)
        return result
    raise FreezeError("source records contain unsupported value types")


def _load_arrow(source: Path) -> tuple[list[dict[str, Any]], str]:
    files = _source_files(source, arrow=True)
    revision = _source_revision(source, files)
    try:
        import pyarrow as pa  # type: ignore[import-untyped]
        import pyarrow.ipc as ipc  # type: ignore[import-untyped]
    except ImportError as exc:
        raise FreezeError("PyArrow is required to read official Arrow caches") from exc
    records: list[dict[str, Any]] = []
    schema: pa.Schema | None = None
    for file in files:
        try:
            with pa.memory_map(str(file), "r") as mapped:
                try:
                    table = ipc.open_file(mapped).read_all()
                except pa.ArrowInvalid:
                    mapped.seek(0)
                    table = ipc.open_stream(mapped).read_all()
        except (OSError, pa.ArrowException) as exc:
            raise FreezeError("an official Arrow cache is unreadable") from exc
        if schema is None:
            schema = table.schema
        elif not schema.equals(table.schema, check_metadata=False):
            raise FreezeError("official Arrow cache schema drift was detected")
        for raw in table.to_pylist():
            safe = _json_safe(raw)
            if not isinstance(safe, dict):
                raise FreezeError("official Arrow records must be objects")
            records.append(cast(dict[str, Any], safe))
    if not records:
        raise FreezeError("an official Arrow cache is empty")
    return records, revision


def _load_jsonl(source: Path) -> tuple[list[dict[str, Any]], str]:
    files = _source_files(source, arrow=False)
    revision = _source_revision(source, files)
    records: list[dict[str, Any]] = []
    expected_keys: set[str] | None = None
    for file in files:
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise FreezeError("a private JSONL source is unreadable") from exc
        for line in lines:
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise FreezeError("a private source is not valid JSONL") from exc
            safe = _json_safe(raw)
            if not isinstance(safe, dict):
                raise FreezeError("private source records must be objects")
            keys = set(safe)
            if expected_keys is None:
                expected_keys = keys
            elif keys != expected_keys:
                raise FreezeError("private source schema drift was detected")
            records.append(cast(dict[str, Any], safe))
    if not records:
        raise FreezeError("a private JSONL source is empty")
    return records, revision


def _record_id(record: Mapping[str, Any], contract: FamilyContract) -> str:
    for field in contract.id_fields:
        value = record.get(field)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            identifier = str(value).strip()
            if identifier:
                return identifier
    raise FreezeError("a source record is missing an immutable sample ID")


def _subset(family: str, record: Mapping[str, Any]) -> str:
    if family == "mmlu_pro":
        category = record.get("category")
        if not isinstance(category, str) or category not in _MMLU_SUBSETS:
            raise FreezeError("MMLU-Pro category coverage does not match the frozen contract")
        existing = record.get("subset")
        if existing is not None and existing != category:
            raise FreezeError("MMLU-Pro category and subset fields disagree")
        return category
    expected = family if family in _CUSTOM_FAMILIES else "default"
    existing = record.get("subset")
    if existing is not None and existing != expected:
        raise FreezeError("a source subset does not match the family contract")
    return expected


def _prepare_records(
    family: str, records: Sequence[Mapping[str, Any]], revision: str
) -> list[dict[str, Any]]:
    contract = _CONTRACTS[family]
    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        identifier = _record_id(record, contract)
        if identifier in seen:
            raise FreezeError("duplicate sample IDs are refused")
        seen.add(identifier)
        subset = _subset(family, record)
        prepared_record = dict(record)
        existing_id = prepared_record.get("sample_id")
        if existing_id is not None and str(existing_id).strip() != identifier:
            raise FreezeError("a source sample ID conflicts with its canonical ID")
        prepared_record["sample_id"] = identifier
        prepared_record["subset"] = subset
        if family in _CUSTOM_FAMILIES:
            try:
                evalscope_exact.validate_private_record(family, prepared_record)
            except ValueError as exc:
                raise FreezeError("a private exact-scorer record is invalid") from exc
        prepared.append(prepared_record)
    if family == "mmlu_pro" and {record["subset"] for record in prepared} != set(_MMLU_SUBSETS):
        raise FreezeError("MMLU-Pro category coverage does not match the frozen contract")
    prepared.sort(key=lambda item: _selection_key(revision, family, cast(str, item["sample_id"])))
    return prepared


def _selection_key(revision: str, family: str, identifier: str) -> tuple[str, str]:
    digest = _sha256(f"{revision}\0{family}\0{identifier}".encode())
    return digest, identifier


def _select(
    family: str, records: Sequence[dict[str, Any]], revision: str
) -> tuple[list[dict[str, Any]], list[str]]:
    contract = _CONTRACTS[family]
    if family == "mmlu_pro":
        heldout: list[dict[str, Any]] = []
        for subset in _MMLU_SUBSETS:
            candidates = [record for record in records if record["subset"] == subset]
            if not candidates:
                raise FreezeError("MMLU-Pro is missing a required category")
            candidates.sort(
                key=lambda item: _selection_key(revision, family, cast(str, item["sample_id"]))
            )
            heldout.append(candidates[0])
        heldout_ids = {cast(str, record["sample_id"]) for record in heldout}
        calibration_pool = [
            record for record in records if cast(str, record["sample_id"]) not in heldout_ids
        ]
        if not calibration_pool:
            raise FreezeError("MMLU-Pro needs a disjoint calibration sample")
        calibration = [cast(str, calibration_pool[0]["sample_id"])]
        return heldout, calibration
    if len(records) < contract.count + 1:
        raise FreezeError("a family has insufficient rows for held-out and calibration sets")
    heldout = list(records[: contract.count])
    return heldout, [cast(str, records[contract.count]["sample_id"])]


def _mkdir_private(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    path.chmod(0o700)


def _write_private(path: Path, data: bytes) -> str:
    if path.exists() or path.is_symlink():
        raise FreezeError("overwrite is refused")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    path.chmod(0o600)
    return _sha256(data)


def _parse_revisions(values: Sequence[str], label: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        family, separator, revision = value.partition("=")
        if separator != "=" or family not in _CONTRACTS or not revision.strip():
            raise FreezeError(f"{label} must explicitly name every family and revision")
        if family in parsed:
            raise FreezeError(f"duplicate {label} entries are refused")
        parsed[family] = revision.strip()
    if set(parsed) != set(_FAMILY_ORDER):
        raise FreezeError(f"{label} must explicitly name every family and revision")
    return parsed


def _profile_fields(family: str) -> dict[str, Any]:
    contract = _CONTRACTS[family]
    fields: dict[str, Any] = {
        "sample_count": contract.count,
        "dataset_id": contract.dataset_id,
        "evidence_split": "held_out",
        "evalscope_dataset": contract.evalscope_dataset,
        "evalscope_split": contract.split,
        "evalscope_subsets": list(contract.subsets),
        "few_shot_num": 0,
        "few_shot_random": False,
        "prompt_template_revision": contract.prompt_revision,
        "scorer_revision": contract.scorer_revision,
        "metric_variant": contract.metric_variant,
    }
    if family in _CUSTOM_FAMILIES:
        fields.update(
            {
                "adapter_id": contract.evalscope_dataset,
                "adapter_revision": evalscope_exact.ADAPTER_REVISION,
            }
        )
    return fields


def _freeze_family(
    *,
    family: str,
    source: Path,
    stage: Path,
    license_revision: str,
    contamination_revision: str,
    adapter_source_sha256: str,
) -> tuple[str, str, int]:
    loader = _load_jsonl if family in _CUSTOM_FAMILIES else _load_arrow
    raw_records, revision = loader(source)
    records = _prepare_records(family, raw_records, revision)
    heldout, calibration_ids = _select(family, records, revision)
    contract = _CONTRACTS[family]
    dataset_dir = stage / "datasets" / family
    _mkdir_private(dataset_dir)
    records_name = f"{contract.split}.jsonl"
    records_raw = b"".join(_canonical_json(record) for record in heldout)
    records_sha = _write_private(dataset_dir / records_name, records_raw)
    ordered_ids = [cast(str, record["sample_id"]) for record in heldout]
    ordered_subsets = [cast(str, record["subset"]) for record in heldout]
    dataset_index = {
        "schema_version": 1,
        "family": family,
        "evidence_split": "held_out",
        "evalscope_split": contract.split,
        "evalscope_subsets": list(contract.subsets),
        "record_count": contract.count,
        "records_path": records_name,
        "records_sha256": records_sha,
        "sample_id_field": "sample_id",
        "subset_field": "subset",
        "ordered_sample_ids": ordered_ids,
        "ordered_subsets": ordered_subsets,
    }
    _write_private(dataset_dir / "selection-index.json", _canonical_json(dataset_index))
    relative_dataset_dir = Path("benchmarks/core/datasets") / family
    relative_dataset = (
        relative_dataset_dir / records_name if family in _CUSTOM_FAMILIES else relative_dataset_dir
    )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "family": family,
        **_profile_fields(family),
        "dataset_revision": revision,
        "dataset_path": relative_dataset.as_posix(),
        "dataset_index_path": (relative_dataset_dir / "selection-index.json").as_posix(),
        "dataset_tree_sha256": _tree_sha256(dataset_dir),
        "ordered_sample_ids": ordered_ids,
        "ordered_sample_ids_sha256": _selection_sha256(ordered_ids),
        "frozen_before_tuning": True,
        "contamination_review_revision": contamination_revision,
        "repeats": 1,
        "all_attempts_in_denominator": True,
        "license_authorized": True,
        "license_authorization_revision": license_revision,
        "calibration_ids": calibration_ids,
        "heldout_ids": ordered_ids,
    }
    if family in _CUSTOM_FAMILIES:
        manifest.update(
            {
                "adapter_id": contract.evalscope_dataset,
                "adapter_revision": evalscope_exact.ADAPTER_REVISION,
                "adapter_source_sha256": adapter_source_sha256,
            }
        )
    manifest_relative = Path("benchmarks/core/manifests") / f"{family}.json"
    manifest_path = stage / "manifests" / f"{family}.json"
    manifest_hash = _write_private(manifest_path, _canonical_json(manifest))
    return manifest_relative.as_posix(), manifest_hash, contract.count


def _validation_profile(manifest_set_sha256: str) -> dict[str, Any]:
    return {
        "suite": "core",
        "runner": "evalscope-1.12",
        "evalscope_version": "1.12.0",
        "model_alias": "racecraft-splash-local",
        "eval_type": "openai_api",
        "api_path": "/v1",
        "eval_batch_size": 1,
        "transport_retries": 0,
        "repetitions": 1,
        "cache_policy": "disabled",
        "judge_policy": "disabled",
        "failure_policy": "all_attempts_in_denominator",
        "inspect_fallback": "unavailable",
        "max_output_tokens": 4096,
        "session_generated_token_cap": 250_000,
        "manifest_set_path": "benchmarks/core/manifest-set.json",
        "manifest_set_sha256": manifest_set_sha256,
        "families": {family: _profile_fields(family) for family in _FAMILY_ORDER},
    }


def freeze(args: argparse.Namespace) -> dict[str, object]:
    repo_root = Path(args.repo_root).resolve(strict=True)
    if not _git_ancestor(repo_root):
        raise FreezeError("the supplied repository root is not a Git checkout")
    output_arg = Path(args.output_root)
    if output_arg.exists() or output_arg.is_symlink():
        output_root = _secure_existing(output_arg, expected_directory=True)
        if stat.S_IMODE(output_root.stat().st_mode) & 0o077:
            raise FreezeError("the private output root must be owner-only")
    else:
        parent = output_arg.parent.resolve(strict=True)
        if _git_ancestor(parent):
            raise FreezeError("source and output paths must remain outside Git")
        output_arg.mkdir(mode=0o700)
        output_root = output_arg.resolve(strict=True)
    if output_root == repo_root or output_root.is_relative_to(repo_root):
        raise FreezeError("source and output paths must remain outside Git")
    licenses = _parse_revisions(args.authorize_license, "license authorization")
    contamination = _parse_revisions(args.contamination_review, "contamination review")
    source_values = {
        "gpqa_diamond": args.gpqa_source,
        "ifeval": args.ifeval_source,
        "mmlu_pro": args.mmlu_pro_source,
        "tool_json": args.tool_json_source,
        "context": args.context_source,
    }
    if any(value is None for value in source_values.values()):
        raise FreezeError("all five local sources are required; GPQA authorization is fail-closed")
    sources = {
        family: _secure_existing(Path(cast(str, value))) for family, value in source_values.items()
    }
    benchmarks_dir = output_root / "benchmarks"
    if benchmarks_dir.exists() or benchmarks_dir.is_symlink():
        mode = benchmarks_dir.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise FreezeError("the private output tree contains an unsafe entry")
        if not benchmarks_dir.resolve(strict=True).is_relative_to(output_root):
            raise FreezeError("the private output tree escaped its root")
    target = output_root / "benchmarks" / "core"
    if target.exists() or target.is_symlink():
        raise FreezeError("overwrite is refused")
    stage = output_root / f".core-freeze-stage-{uuid.uuid4().hex}"
    adapter_path = Path(evalscope_exact.__file__)
    adapter_source_sha256 = _sha256(adapter_path.read_bytes())
    manifest_entries: dict[str, dict[str, str]] = {}
    counts: dict[str, int] = {}
    installed = False
    try:
        _mkdir_private(stage)
        _mkdir_private(stage / "datasets")
        _mkdir_private(stage / "manifests")
        for family in _FAMILY_ORDER:
            relative, digest, count = _freeze_family(
                family=family,
                source=sources[family],
                stage=stage,
                license_revision=licenses[family],
                contamination_revision=contamination[family],
                adapter_source_sha256=adapter_source_sha256,
            )
            manifest_entries[family] = {
                "manifest_path": relative,
                "manifest_sha256": digest,
            }
            counts[family] = count
        manifest_set = {"schema_version": 1, "families": manifest_entries}
        manifest_set_hash = _write_private(
            stage / "manifest-set.json", _canonical_json(manifest_set)
        )
        benchmarks_dir.mkdir(mode=0o700, exist_ok=True)
        benchmarks_dir.chmod(0o700)
        if _sha256(adapter_path.read_bytes()) != adapter_source_sha256:
            raise FreezeError("the exact adapter changed while inputs were being frozen")
        stage.rename(target)
        installed = True
        profile = _validation_profile(manifest_set_hash)
        profile_families = benchmarks._validate_profile(profile)
        _families, validated_hash = benchmarks._validated_families(
            profile, profile_families, output_root
        )
        if validated_hash != manifest_set_hash:
            raise FreezeError("the installed manifest set failed exact validation")
    except Exception:
        if stage.exists() and not stage.is_symlink():
            shutil.rmtree(stage)
        if installed and target.exists() and not target.is_symlink():
            shutil.rmtree(target)
        raise
    return {
        "status": "frozen",
        "families": counts,
        "manifest_set_sha256": manifest_set_hash,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze private core inputs without downloading or running inference."
    )
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--ifeval-source", required=True)
    parser.add_argument("--mmlu-pro-source", required=True)
    parser.add_argument("--gpqa-source")
    parser.add_argument("--tool-json-source", required=True)
    parser.add_argument("--context-source", required=True)
    parser.add_argument(
        "--authorize-license",
        action="append",
        default=[],
        metavar="FAMILY=REVISION",
    )
    parser.add_argument(
        "--contamination-review",
        action="append",
        default=[],
        metavar="FAMILY=REVISION",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = freeze(_parser().parse_args(argv))
    except FreezeError as exc:
        print(f"freeze refused: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("freeze refused: unexpected private-state failure", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
