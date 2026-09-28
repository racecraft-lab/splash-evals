#!/usr/bin/env python3
"""Freeze pinned SWE-bench sources into deterministic private-state inputs.

The command is offline and fail-closed. It never prints source records, task IDs,
prompts, patches, tests, or filesystem paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

_REPO_ROOT = Path(__file__).resolve().parents[1]
_REVISION_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_INSTANCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}")
_REQUIRED_FIELDS = {
    "instance_id",
    "repo",
    "base_commit",
    "problem_statement",
    "patch",
    "test_patch",
    "version",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "environment_setup_commit",
}
VERIFIED_TASK_COUNT = 500
MAX_QUALIFICATION_TASKS = 10
QUALIFICATION_SELECTION_SEED = "racecraft-swebench-qualification-v1"
QUALIFICATION_SELECTION_POLICY_VERSION = 1
VERIFIED_SELECTION_POLICY_VERSION = 1


class FreezeError(ValueError):
    """Raised when SWE-bench inputs cannot be frozen without guessing."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FreezeError("source records must contain finite JSON values") from exc


def _git_ancestor(path: Path) -> bool:
    current = path if path.is_dir() else path.parent
    while True:
        marker = current / ".git"
        if marker.exists() or marker.is_symlink():
            return True
        if current.parent == current:
            return False
        current = current.parent


def _private_file(path: Path, label: str) -> Path:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise FreezeError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(mode):
        raise FreezeError("symbolic-link inputs are refused")
    if not stat.S_ISREG(mode):
        raise FreezeError(f"{label} must be a regular file")
    resolved = path.resolve(strict=True)
    if _git_ancestor(resolved):
        raise FreezeError("source and output paths must remain outside Git")
    return resolved


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FreezeError("source records contain duplicate JSON keys")
        result[key] = value
    return result


def _validate_test_list(value: object, label: str) -> None:
    if isinstance(value, str):
        return
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return
    raise FreezeError(f"{label} must be a string or list of strings")


def _validate_record(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FreezeError("source JSONL records must be objects")
    record = cast(dict[str, Any], value)
    if not _REQUIRED_FIELDS.issubset(record):
        raise FreezeError("source records are missing required official fields")
    instance_id = record["instance_id"]
    if not isinstance(instance_id, str) or _INSTANCE_RE.fullmatch(instance_id) is None:
        raise FreezeError("source records contain an unsafe instance ID")
    for field in ("repo", "problem_statement", "version"):
        if not isinstance(record[field], str) or not record[field].strip():
            raise FreezeError(f"source record {field} must be nonblank")
    for field in ("patch", "test_patch"):
        if not isinstance(record[field], str):
            raise FreezeError(f"source record {field} must be a string")
    for field in ("base_commit", "environment_setup_commit"):
        if not isinstance(record[field], str) or _REVISION_RE.fullmatch(record[field]) is None:
            raise FreezeError(f"source record {field} must be an immutable 40-hex commit")
    _validate_test_list(record["FAIL_TO_PASS"], "FAIL_TO_PASS")
    _validate_test_list(record["PASS_TO_PASS"], "PASS_TO_PASS")
    _canonical(record)
    return record


def _load_source(path: Path, expected_sha256: object, label: str) -> list[dict[str, Any]]:
    source = _private_file(path, label)
    raw = source.read_bytes()
    if not isinstance(expected_sha256, str) or _SHA256_RE.fullmatch(expected_sha256) is None:
        raise FreezeError(f"{label} source SHA-256 must be exact lowercase hex")
    if _sha256(raw) != expected_sha256:
        raise FreezeError(f"{label} source SHA-256 does not match pinned bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FreezeError(f"{label} source is not UTF-8 JSONL") from exc
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line, object_pairs_hook=_reject_duplicate_keys)
        except (json.JSONDecodeError, FreezeError) as exc:
            raise FreezeError(f"{label} source is invalid JSONL") from exc
        records.append(_validate_record(value))
    if not records:
        raise FreezeError(f"{label} source is empty")
    ids = [cast(str, record["instance_id"]) for record in records]
    if len(set(ids)) != len(ids):
        raise FreezeError(f"{label} source must contain unique instance IDs")
    return records


def _load_qualification_exclusions(
    path: Path, expected_sha256: object
) -> tuple[set[str], str, str]:
    source = _private_file(path, "prior qualification exclusions")
    raw = source.read_bytes()
    if not isinstance(expected_sha256, str) or _SHA256_RE.fullmatch(expected_sha256) is None:
        raise FreezeError("prior qualification exclusions SHA-256 must be exact lowercase hex")
    source_sha256 = _sha256(raw)
    if source_sha256 != expected_sha256:
        raise FreezeError("prior qualification exclusions SHA-256 does not match pinned bytes")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, FreezeError) as exc:
        raise FreezeError("prior qualification exclusions are invalid JSON") from exc
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "prior_manifest_sha256",
        "instance_ids",
    }:
        raise FreezeError("prior qualification exclusions have an invalid shape")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise FreezeError("prior qualification exclusions schema version is unsupported")
    prior_manifest_sha256 = value["prior_manifest_sha256"]
    if (
        not isinstance(prior_manifest_sha256, str)
        or _SHA256_RE.fullmatch(prior_manifest_sha256) is None
    ):
        raise FreezeError("prior qualification manifest SHA-256 must be exact lowercase hex")
    instance_ids = value["instance_ids"]
    if (
        not isinstance(instance_ids, list)
        or not 1 <= len(instance_ids) <= MAX_QUALIFICATION_TASKS
        or any(
            not isinstance(instance_id, str) or _INSTANCE_RE.fullmatch(instance_id) is None
            for instance_id in instance_ids
        )
        or instance_ids != sorted(set(instance_ids))
    ):
        raise FreezeError("prior qualification IDs must be 1 to 10 unique sorted instance IDs")
    canonical = _canonical(value)
    if raw not in (canonical, canonical + b"\n"):
        raise FreezeError("prior qualification exclusions must use canonical JSON")
    return set(instance_ids), prior_manifest_sha256, source_sha256


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FreezeError(f"{label} must be nonblank")
    return value.strip()


def _revision(value: object, label: str) -> str:
    revision = _nonblank(value, label)
    if _REVISION_RE.fullmatch(revision) is None:
        raise FreezeError(f"{label} must be an immutable 40-hex commit")
    return revision


def _qualification(
    records: Sequence[dict[str, Any]],
    verified_ids: set[str],
    prior_qualification_ids: set[str],
    count: int,
) -> list[dict[str, Any]]:
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or not 1 <= count <= MAX_QUALIFICATION_TASKS
    ):
        raise FreezeError("qualification count must be between 1 and 10")
    candidates = [
        record
        for record in records
        if record["instance_id"] not in verified_ids
        and record["instance_id"] not in prior_qualification_ids
    ]
    if len(candidates) < count:
        raise FreezeError("parent source has insufficient tasks disjoint from Verified")
    ranked = sorted(
        candidates,
        key=lambda record: (
            _sha256(f"{QUALIFICATION_SELECTION_SEED}\0{record['instance_id']}".encode()),
            cast(str, record["instance_id"]),
        ),
    )[:count]
    return sorted(ranked, key=lambda record: cast(str, record["instance_id"]))


def _selection_policy(
    mode: str,
    dataset_id: str,
    *,
    prior_manifest_sha256: str | None = None,
    prior_exclusions_sha256: str | None = None,
) -> dict[str, object]:
    if mode == "qualification":
        if (
            not isinstance(prior_manifest_sha256, str)
            or _SHA256_RE.fullmatch(prior_manifest_sha256) is None
            or not isinstance(prior_exclusions_sha256, str)
            or _SHA256_RE.fullmatch(prior_exclusions_sha256) is None
        ):
            raise FreezeError("qualification selection requires pinned prior exclusions")
        return {
            "schema_version": QUALIFICATION_SELECTION_POLICY_VERSION,
            "algorithm": "sha256_utf8_seed_nul_instance_id",
            "seed": QUALIFICATION_SELECTION_SEED,
            "candidate_dataset_id": dataset_id,
            "candidate_split": "test",
            "exclusion": "swebench_verified_500_and_prior_qualification_instance_ids",
            "prior_qualification_manifest_sha256": prior_manifest_sha256,
            "prior_qualification_exclusions_sha256": prior_exclusions_sha256,
            "rank_tiebreaker": "instance_id_ascending",
            "output_order": "instance_id_ascending",
            "evidence_class": "qualification_only_non_capability",
        }
    return {
        "schema_version": VERIFIED_SELECTION_POLICY_VERSION,
        "algorithm": "full_test_split_source_order",
        "candidate_dataset_id": dataset_id,
        "candidate_split": "test",
        "output_order": "source_order",
        "evidence_class": "held_out_capability",
    }


def _mkdir_private(path: Path) -> None:
    path.mkdir(mode=0o700, parents=False, exist_ok=False)
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
        path.unlink(missing_ok=True)
        raise
    path.chmod(0o600)
    return _sha256(data)


def _records_bytes(records: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(_canonical(record) + b"\n" for record in records)


def _task(record: Mapping[str, Any]) -> dict[str, str]:
    canonical = _canonical(record)
    return {
        "instance_id": cast(str, record["instance_id"]),
        "base_commit": cast(str, record["base_commit"]),
        "repository": cast(str, record["repo"]),
        "problem_statement_sha256": _sha256(cast(str, record["problem_statement"]).encode("utf-8")),
        "record_sha256": _sha256(canonical),
    }


def _freeze_set(
    *,
    name: str,
    records: Sequence[dict[str, Any]],
    stage: Path,
    dataset_id: str,
    dataset_revision: str,
    source_sha256: str,
    selection_policy: Mapping[str, object],
    verified_ids: Sequence[str],
    license_revision: str,
) -> tuple[str, str]:
    directory = stage / name
    _mkdir_private(directory)
    records_raw = _records_bytes(records)
    records_sha = _write_private(directory / "records.jsonl", records_raw)
    tasks = [_task(record) for record in records]
    ids = [task["instance_id"] for task in tasks]
    manifest = {
        "schema_version": 1,
        "benchmark": "swebench_verified",
        "dataset_id": dataset_id,
        "dataset_revision": dataset_revision,
        "source_sha256": source_sha256,
        "records_path": f"swebench/{name}/records.jsonl",
        "records_sha256": records_sha,
        "selection_policy": selection_policy,
        "split": "test",
        "task_count": len(tasks),
        "tasks": tasks,
        "ordered_instance_ids_sha256": _sha256(
            json.dumps(ids, separators=(",", ":")).encode("utf-8")
        ),
        "verified_500_instance_ids": list(verified_ids),
        "verified_500_ids_sha256": _sha256(
            json.dumps(list(verified_ids), separators=(",", ":")).encode("utf-8")
        ),
        "frozen_before_tuning": True,
        "license_authorized": True,
        "license_authorization_revision": license_revision,
    }
    manifest_sha = _write_private(directory / "manifest.json", _canonical(manifest))
    return records_sha, manifest_sha


def freeze(args: argparse.Namespace) -> dict[str, object]:
    repo_root = Path(args.repo_root).resolve(strict=True)
    if not _git_ancestor(repo_root):
        raise FreezeError("the supplied repository root is not a Git checkout")
    output_arg = Path(args.output_root)
    if output_arg.exists() or output_arg.is_symlink():
        try:
            mode = output_arg.lstat().st_mode
        except OSError as exc:
            raise FreezeError("the private output root is unavailable") from exc
        if stat.S_ISLNK(mode):
            raise FreezeError("symbolic-link outputs are refused")
        if not stat.S_ISDIR(mode):
            raise FreezeError("the private output root must be a directory")
        output_root = output_arg.resolve(strict=True)
        if stat.S_IMODE(output_root.stat().st_mode) & 0o077:
            raise FreezeError("the private output root must be owner-only")
    else:
        output_parent = output_arg.parent.resolve(strict=True)
        if _git_ancestor(output_parent):
            raise FreezeError("source and output paths must remain outside Git")
        output_arg.mkdir(mode=0o700)
        output_root = output_arg.resolve(strict=True)
    if (
        _git_ancestor(output_root)
        or output_root == repo_root
        or output_root.is_relative_to(repo_root)
    ):
        raise FreezeError("source and output paths must remain outside Git")

    target = output_root / "swebench"
    if target.exists() or target.is_symlink():
        raise FreezeError("overwrite is refused")
    receipt = _private_file(Path(args.license_authorization_receipt), "license receipt")
    if not receipt.read_bytes().strip():
        raise FreezeError("license authorization receipt must be nonempty")
    license_revision = _nonblank(
        args.license_authorization_revision, "license authorization revision"
    )
    verified_revision = _revision(args.verified_dataset_revision, "Verified dataset revision")
    parent_revision = _revision(args.parent_dataset_revision, "parent dataset revision")
    verified_source_sha = _nonblank(args.verified_source_sha256, "Verified source SHA-256")
    parent_source_sha = _nonblank(args.parent_source_sha256, "parent source SHA-256")
    verified = _load_source(Path(args.verified_source), verified_source_sha, "Verified")
    if len(verified) != VERIFIED_TASK_COUNT:
        raise FreezeError("Verified source must contain exactly 500 unique rows")
    parent_records = _load_source(Path(args.parent_source), parent_source_sha, "parent")
    prior_qualification_ids, prior_manifest_sha, prior_exclusions_sha = (
        _load_qualification_exclusions(
            Path(args.prior_qualification_exclusions),
            args.prior_qualification_exclusions_sha256,
        )
    )
    verified_ids = [cast(str, record["instance_id"]) for record in verified]
    verified_id_set = set(verified_ids)
    parent_id_set = {cast(str, record["instance_id"]) for record in parent_records}
    if prior_qualification_ids & verified_id_set:
        raise FreezeError("prior qualification IDs must be disjoint from Verified")
    if not prior_qualification_ids.issubset(parent_id_set):
        raise FreezeError("prior qualification IDs must exist in the parent source")
    qualification = _qualification(
        parent_records,
        verified_id_set,
        prior_qualification_ids,
        args.qualification_count,
    )
    qualification_ids = {cast(str, record["instance_id"]) for record in qualification}
    if verified_id_set & qualification_ids or prior_qualification_ids & qualification_ids:
        raise FreezeError(
            "qualification tasks must be disjoint from Verified and prior qualification"
        )

    stage = output_root / f".swebench-freeze-stage-{uuid.uuid4().hex}"
    installed = False
    try:
        _mkdir_private(stage)
        verified_records_sha, verified_manifest_sha = _freeze_set(
            name="verified",
            records=verified,
            stage=stage,
            dataset_id=_nonblank(args.verified_dataset_id, "Verified dataset ID"),
            dataset_revision=verified_revision,
            source_sha256=verified_source_sha,
            selection_policy=_selection_policy(
                "verified", _nonblank(args.verified_dataset_id, "Verified dataset ID")
            ),
            verified_ids=verified_ids,
            license_revision=license_revision,
        )
        qualification_records_sha, qualification_manifest_sha = _freeze_set(
            name="qualification",
            records=qualification,
            stage=stage,
            dataset_id=_nonblank(args.parent_dataset_id, "parent dataset ID"),
            dataset_revision=parent_revision,
            source_sha256=parent_source_sha,
            selection_policy=_selection_policy(
                "qualification",
                _nonblank(args.parent_dataset_id, "parent dataset ID"),
                prior_manifest_sha256=prior_manifest_sha,
                prior_exclusions_sha256=prior_exclusions_sha,
            ),
            verified_ids=verified_ids,
            license_revision=license_revision,
        )
        stage.rename(target)
        installed = True
    except Exception:
        if stage.exists() and not stage.is_symlink():
            shutil.rmtree(stage)
        if installed and target.exists() and not target.is_symlink():
            shutil.rmtree(target)
        raise
    return {
        "status": "frozen",
        "counts": {"qualification": len(qualification), "verified": len(verified)},
        "hashes": {
            "qualification_manifest_sha256": qualification_manifest_sha,
            "qualification_records_sha256": qualification_records_sha,
            "verified_manifest_sha256": verified_manifest_sha,
            "verified_records_sha256": verified_records_sha,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze pinned SWE-bench JSONL inputs into private state."
    )
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--verified-source", required=True)
    parser.add_argument("--verified-source-sha256", required=True)
    parser.add_argument("--verified-dataset-id", required=True)
    parser.add_argument("--verified-dataset-revision", required=True)
    parser.add_argument("--parent-source", required=True)
    parser.add_argument("--parent-source-sha256", required=True)
    parser.add_argument("--parent-dataset-id", required=True)
    parser.add_argument("--parent-dataset-revision", required=True)
    parser.add_argument("--prior-qualification-exclusions", required=True)
    parser.add_argument("--prior-qualification-exclusions-sha256", required=True)
    parser.add_argument("--license-authorization-receipt", required=True)
    parser.add_argument("--license-authorization-revision", required=True)
    parser.add_argument("--qualification-count", required=True, type=int)
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
