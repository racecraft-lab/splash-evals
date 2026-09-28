#!/usr/bin/env python3
"""Prepare private, frozen SWE-bench qualification or Verified image contexts.

This command does not build images or run inference. It validates either the
frozen ten-task, non-Verified qualification selection (``--scope qualification``)
or the frozen ordered Verified 500 (``--scope verified``) against the pinned
official task repository, then emits owner-only task/grader contexts and a build
specification for ``build_swebench_images.py``. Frozen task/grader bases remain
linux/amd64; the separately pinned local control image is inspected as
linux/arm64/v8.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import yaml

TASK_REPOSITORY_REVISION = "3d07b464b7b311a0cbfb5ed5b2d8a3b96f84a33d"
PARENT_DATASET_REVISION = "c6fe717fd7a4c3ac1daa4055a4fd082c6a1d28a2"
VERIFIED_DATASET_REVISION = "78f471bf655a3137b2e8a75af1501690ec009ec3"
SWEBENCH_SOURCE_REVISION = "490635b2e9e775dca1e1d6b40ce9dbcff91e780f"
SWEBENCH_VERSION = "5.0.2"
PLATFORM = "linux/amd64"
CONTROL_PLATFORM = "linux/arm64/v8"
SCORER_ENTRYPOINT_PATH = "/opt/racecraft/scorer_entrypoint.py"
SCORER_ENTRYPOINT_SHA256 = "e313d7fdf0ff500562cddaccd4feada8d9cba4c8b4adf838685fd893e0896858"
# Containers run as 65532; git refuses a root-owned checkout and files must be editable.
TESTBED_OWNERSHIP_STEP = "RUN chown -R 65532:65532 /testbed"
# Upstream's default SWE-bench config sets BASH_ENV=/root/.bashrc to activate `testbed`;
# the non-root agent needs a readable copy of that same file.
TESTBED_ACTIVATION_STEP = "RUN install -m 0444 /root/.bashrc /opt/racecraft/bashrc"
# Upstream grades as root, whose passwd entry tools such as getpass rely on; without an entry
# for 65532, Django's createsuperuser default-username test loops forever.
TESTBED_USER_STEP = (
    "RUN grep -q '^[^:]*:[^:]*:65532:' /etc/passwd"
    " || { echo 'nonroot:x:65532:65532:nonroot:/tmp/home:/bin/sh' >> /etc/passwd"
    " && echo 'nonroot:x:65532:' >> /etc/group; }"
)
# Some official images ship untracked files in /testbed; the grader compares against this.
TESTBED_BASELINE_STEP = (
    "RUN mkdir -p /opt/racecraft"
    " && git -C /testbed -c safe.directory=/testbed --no-optional-locks"
    " status --porcelain --untracked-files=all > /opt/racecraft/testbed-baseline.txt"
    " && chown 0:0 /opt/racecraft/testbed-baseline.txt"
    " && chmod 0444 /opt/racecraft/testbed-baseline.txt"
)
GENERATED_EVAL_CONTEXT_NAME = "test-spec-eval.sh"
GENERATED_EVAL_IMAGE_PATH = "/opt/racecraft/grader/test-spec-eval.sh"
TEST_EXIT_VARIABLE = "SWEBENCH_TEST_EXIT_CODE"
TEST_END_MARKER_LINE = ": '>>>>> End Test Output'"
TEST_EXIT_MARKER_LINE = 'echo ">>>>> Test Exit Code: $SWEBENCH_TEST_EXIT_CODE"'
TASK_COUNT = 10
VERIFIED_TASK_COUNT = 500
SCOPES = ("qualification", "verified")

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_IMAGE_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_REVISION_RE = re.compile(r"[0-9a-f]{40}")
_INSTANCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}")
_VALIDATION_KEYS = {
    "schema_version",
    "command",
    "swebench_version",
    "task_repository_revision",
    "status",
    "checked_instance_ids",
    "output_sha256",
}
_BASE_IMAGE_KEYS = {
    "instance_id",
    "image_reference",
    "image_digest",
    "dockerfile_sha256",
}
_SPEC_LIMIT_KEYS = {
    "memory_bytes",
    "cpus",
    "pids_limit",
    "nofile_limit",
    "tmpfs_bytes",
}
_HOST_TEST_SPEC_FIELDS = {
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
_PREPARE_RESPONSE_KEYS = {
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


class PreparationError(ValueError):
    """Raised when private qualification inputs violate the frozen contract."""


RevisionReader = Callable[[Path], str]
ControlRunner = Callable[[Sequence[str], bytes | None], subprocess.CompletedProcess[bytes]]


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
        raise PreparationError("value is not canonical JSON") from exc


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PreparationError(f"{label} must be an object")
    return cast(Mapping[str, Any], value)


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise PreparationError(f"{label} fields do not match the frozen schema")


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _private_file(path: Path, label: str, expected_sha256: str | None = None) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise PreparationError(f"{label} must be a regular file")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise PreparationError(f"{label} must be owner-only")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PreparationError(f"{label} is unreadable") from exc
    if expected_sha256 is not None and (
        not _is_sha256(expected_sha256) or _sha256(raw) != expected_sha256
    ):
        raise PreparationError(f"{label} fingerprint does not match frozen bytes")
    return raw


def _json_file(path: Path, label: str, expected_sha256: str) -> Mapping[str, Any]:
    raw = _private_file(path, label, expected_sha256)
    try:
        return _mapping(json.loads(raw), label)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreparationError(f"{label} is invalid JSON") from exc


def _regular_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise PreparationError(f"{label} must be a regular file")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise PreparationError(f"{label} is unreadable") from exc


def _resolve_outside_repo(path: Path, repo: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise PreparationError(f"{label} does not exist") from exc
    if resolved == repo or resolved.is_relative_to(repo):
        raise PreparationError(f"{label} must remain outside the Git checkout")
    return resolved


def _future_output(path: Path, repo: Path) -> Path:
    if path.exists() or path.is_symlink():
        raise PreparationError("overwrite is refused")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise PreparationError("output parent does not exist") from exc
    if parent == repo or parent.is_relative_to(repo):
        raise PreparationError("output must remain outside the Git checkout")
    if parent.is_symlink() or not parent.is_dir() or stat.S_IMODE(parent.stat().st_mode) & 0o077:
        raise PreparationError("output parent must be a real owner-only directory")
    if path.name in {"", ".", ".."}:
        raise PreparationError("output path is unsafe")
    return parent / path.name


def _default_revision_reader(task_repository: Path) -> str:
    try:
        result = subprocess.run(  # noqa: S603 - fixed git argv and validated local path
            ("/usr/bin/git", "-C", str(task_repository), "rev-parse", "HEAD"),
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        status = subprocess.run(  # noqa: S603 - fixed git argv and validated local path
            (
                "/usr/bin/git",
                "-C",
                str(task_repository),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ),
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreparationError("task repository revision is unavailable") from exc
    if status.stdout:
        raise PreparationError("task repository checkout must be clean at the pinned revision")
    return result.stdout.strip()


def _validate_manifest_identity(
    manifest: Mapping[str, Any], *, qualification: bool
) -> list[Mapping[str, Any]]:
    expected_revision = PARENT_DATASET_REVISION if qualification else VERIFIED_DATASET_REVISION
    # A qualification cohort holds at most TASK_COUNT tasks; a continuation may hold fewer.
    expected_count = manifest.get("task_count") if qualification else VERIFIED_TASK_COUNT
    if (
        type(expected_count) is not int
        or not 1 <= expected_count <= (TASK_COUNT if qualification else VERIFIED_TASK_COUNT)
        or manifest.get("schema_version") != 1
        or manifest.get("benchmark") != "swebench_verified"
        or manifest.get("dataset_revision") != expected_revision
        or manifest.get("split") != "test"
        or manifest.get("task_count") != expected_count
    ):
        raise PreparationError("frozen manifest identity or task count is invalid")
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != expected_count:
        raise PreparationError("frozen manifest task list is invalid")
    mapped = [_mapping(task, "manifest task") for task in tasks]
    ids = [task.get("instance_id") for task in mapped]
    if (
        any(
            not isinstance(instance_id, str) or _INSTANCE_RE.fullmatch(instance_id) is None
            for instance_id in ids
        )
        or len(set(ids)) != expected_count
    ):
        raise PreparationError("frozen manifest instance IDs are invalid")
    return mapped


def _load_records(
    path: Path, expected_sha256: object, max_count: int = TASK_COUNT
) -> list[Mapping[str, Any]]:
    if not _is_sha256(expected_sha256):
        raise PreparationError("qualification records fingerprint is invalid")
    raw = _private_file(path, "qualification records", cast(str, expected_sha256))
    records: list[Mapping[str, Any]] = []
    for line in raw.splitlines():
        if not line:
            raise PreparationError("qualification records contain a blank line")
        try:
            record = _mapping(json.loads(line), "qualification record")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PreparationError("qualification records are invalid JSONL") from exc
        if _canonical(record) != line:
            raise PreparationError("qualification records are not canonical JSONL")
        records.append(record)
    if not 1 <= len(records) <= max_count:
        raise PreparationError(f"qualification records must contain one to {max_count} tasks")
    return records


def _validate_selection(
    qualification: Mapping[str, Any],
    verified: Mapping[str, Any],
    records_path: Path,
    license_receipt: Path,
    prior_qualification_manifest_path: Path | None = None,
    prior_qualification_exclusions_path: Path | None = None,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    qualification_tasks = _validate_manifest_identity(qualification, qualification=True)
    verified_tasks = _validate_manifest_identity(verified, qualification=False)
    selected_ids = [cast(str, task["instance_id"]) for task in qualification_tasks]
    verified_ids = [cast(str, task["instance_id"]) for task in verified_tasks]
    if set(selected_ids) & set(verified_ids):
        raise PreparationError("qualification tasks must remain disjoint from Verified 500")
    embedded_verified = qualification.get("verified_500_instance_ids")
    if embedded_verified != verified_ids:
        raise PreparationError("qualification Verified exclusion set drifted")
    expected_verified_sha = _sha256(_canonical(verified_ids))
    if (
        qualification.get("verified_500_ids_sha256") != expected_verified_sha
        or verified.get("verified_500_ids_sha256") != expected_verified_sha
        or verified.get("verified_500_instance_ids") != verified_ids
    ):
        raise PreparationError("Verified exclusion fingerprint drifted")
    if qualification.get("ordered_instance_ids_sha256") != _sha256(_canonical(selected_ids)):
        raise PreparationError("qualification selection ordering drifted")
    policy = _mapping(qualification.get("selection_policy"), "qualification selection policy")
    if (
        policy.get("evidence_class") != "qualification_only_non_capability"
        or policy.get("seed") != "racecraft-swebench-qualification-v1"
        or qualification.get("frozen_before_tuning") is not True
    ):
        raise PreparationError("qualification selection policy is invalid")
    exclusion = policy.get("exclusion")
    if exclusion == "swebench_verified_500_instance_ids":
        if (
            prior_qualification_manifest_path is not None
            or prior_qualification_exclusions_path is not None
        ):
            raise PreparationError("unexpected prior qualification exclusion evidence")
    elif exclusion == "swebench_verified_500_and_prior_qualification_instance_ids":
        prior_manifest_sha = policy.get("prior_qualification_manifest_sha256")
        prior_exclusions_sha = policy.get("prior_qualification_exclusions_sha256")
        if (
            prior_qualification_manifest_path is None
            or prior_qualification_exclusions_path is None
            or not _is_sha256(prior_manifest_sha)
            or not _is_sha256(prior_exclusions_sha)
            or policy.get("schema_version") != 1
            or policy.get("algorithm") != "sha256_utf8_seed_nul_instance_id"
            or policy.get("candidate_dataset_id") != qualification.get("dataset_id")
            or policy.get("candidate_split") != "test"
            or policy.get("rank_tiebreaker") != "instance_id_ascending"
            or policy.get("output_order") != "instance_id_ascending"
        ):
            raise PreparationError("prior qualification selection policy is invalid")
        prior_manifest = _json_file(
            prior_qualification_manifest_path,
            "prior qualification manifest",
            cast(str, prior_manifest_sha),
        )
        prior_tasks = _validate_manifest_identity(prior_manifest, qualification=True)
        prior_ids = [cast(str, task["instance_id"]) for task in prior_tasks]
        prior_policy = _mapping(
            prior_manifest.get("selection_policy"), "prior qualification selection policy"
        )
        if (
            prior_manifest.get("ordered_instance_ids_sha256") != _sha256(_canonical(prior_ids))
            or prior_manifest.get("frozen_before_tuning") is not True
            or prior_manifest.get("license_authorized") is not True
            or prior_manifest.get("verified_500_instance_ids") != verified_ids
            or prior_manifest.get("verified_500_ids_sha256") != expected_verified_sha
            or prior_policy.get("schema_version") != 1
            or prior_policy.get("algorithm") != "sha256_utf8_seed_nul_instance_id"
            or prior_policy.get("seed") != "racecraft-swebench-qualification-v1"
            or prior_policy.get("candidate_dataset_id") != prior_manifest.get("dataset_id")
            or prior_policy.get("candidate_split") != "test"
            or prior_policy.get("exclusion") != "swebench_verified_500_instance_ids"
            or prior_policy.get("rank_tiebreaker") != "instance_id_ascending"
            or prior_policy.get("output_order") != "instance_id_ascending"
            or prior_policy.get("evidence_class") != "qualification_only_non_capability"
            or set(prior_ids) & set(verified_ids)
        ):
            raise PreparationError("prior qualification manifest identity is invalid")
        exclusions = _json_file(
            prior_qualification_exclusions_path,
            "prior qualification exclusions",
            cast(str, prior_exclusions_sha),
        )
        _exact_keys(
            exclusions,
            {"instance_ids", "prior_manifest_sha256", "schema_version"},
            "prior qualification exclusions",
        )
        excluded_prior_ids = exclusions.get("instance_ids")
        if (
            exclusions.get("schema_version") != 1
            or exclusions.get("prior_manifest_sha256") != prior_manifest_sha
            or excluded_prior_ids != prior_ids
            or set(selected_ids) & set(prior_ids)
        ):
            raise PreparationError("prior qualification exclusions do not match the prior manifest")
    else:
        raise PreparationError("qualification selection policy is invalid")
    records = _validated_records_and_license(
        qualification, qualification_tasks, records_path, license_receipt, TASK_COUNT
    )
    return qualification_tasks, records


def _validated_records_and_license(
    manifest: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
    records_path: Path,
    license_receipt: Path,
    max_count: int,
) -> list[Mapping[str, Any]]:
    selected_ids = [cast(str, task["instance_id"]) for task in tasks]
    records = _load_records(records_path, manifest.get("records_sha256"), max_count)
    if [record.get("instance_id") for record in records] != selected_ids:
        raise PreparationError("qualification records do not match the frozen selection")
    for task, record in zip(tasks, records, strict=True):
        expected_task = {
            "instance_id": record.get("instance_id"),
            "base_commit": record.get("base_commit"),
            "repository": record.get("repo"),
            "problem_statement_sha256": _sha256(
                cast(str, record.get("problem_statement", "")).encode()
            ),
            "record_sha256": _sha256(_canonical(record)),
        }
        if dict(task) != expected_task:
            raise PreparationError("qualification source record metadata drifted")
    receipt = _private_file(license_receipt, "license authorization receipt")
    if (
        not receipt.strip()
        or manifest.get("license_authorized") is not True
        or manifest.get("license_authorization_revision") != _sha256(receipt)
    ):
        raise PreparationError("license authorization does not match the frozen manifest")
    return records


def _validate_verified_selection(
    verified: Mapping[str, Any], records_path: Path, license_receipt: Path
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Validate the frozen ordered Verified manifest as the build selection."""
    tasks = _validate_manifest_identity(verified, qualification=False)
    ids = [cast(str, task["instance_id"]) for task in tasks]
    ids_sha = _sha256(_canonical(ids))
    if (
        verified.get("verified_500_instance_ids") != ids
        or verified.get("verified_500_ids_sha256") != ids_sha
        or verified.get("ordered_instance_ids_sha256") != ids_sha
    ):
        raise PreparationError("Verified selection ordering or fingerprint drifted")
    dataset_id = verified.get("dataset_id")
    if (
        not isinstance(dataset_id, str)
        or not dataset_id.strip()
        or verified.get("selection_policy")
        != {
            "schema_version": 1,
            "algorithm": "full_test_split_source_order",
            "candidate_dataset_id": dataset_id,
            "candidate_split": "test",
            "output_order": "source_order",
            "evidence_class": "held_out_capability",
        }
        or verified.get("frozen_before_tuning") is not True
    ):
        raise PreparationError("Verified selection policy is invalid")
    records = _validated_records_and_license(
        verified, tasks, records_path, license_receipt, VERIFIED_TASK_COUNT
    )
    return tasks, records


def _validate_dataset_check(evidence: Mapping[str, Any], instance_ids: Sequence[str]) -> None:
    _exact_keys(evidence, _VALIDATION_KEYS, "dataset check evidence")
    if (
        evidence.get("schema_version") != 1
        or evidence.get("command") != "swebench dataset check"
        or evidence.get("swebench_version") != SWEBENCH_VERSION
        or evidence.get("task_repository_revision") != TASK_REPOSITORY_REVISION
        or evidence.get("status") != "passed"
        or evidence.get("checked_instance_ids") != list(instance_ids)
        or not _is_sha256(evidence.get("output_sha256"))
    ):
        raise PreparationError("dataset check evidence is invalid or did not pass")


def _validate_image_reference(reference: object, digest: object) -> tuple[str, str]:
    if (
        not isinstance(reference, str)
        or not isinstance(digest, str)
        or _IMAGE_DIGEST_RE.fullmatch(digest) is None
        or not reference.endswith(f"@{digest}")
        or reference.count("@") != 1
        or any(character.isspace() for character in reference)
    ):
        raise PreparationError("base task image must use one immutable sha256 reference")
    return reference, digest


def _image_repository(reference: object) -> str | None:
    if not isinstance(reference, str) or not reference or any(c.isspace() for c in reference):
        return None
    name = reference.split("@", 1)[0]
    final_component = name.rsplit("/", 1)[-1]
    if ":" in final_component:
        name = name[: -len(final_component)] + final_component.rsplit(":", 1)[0]
    return name or None


def _base_images(
    evidence: Mapping[str, Any], instance_ids: Sequence[str]
) -> dict[str, Mapping[str, Any]]:
    if (
        set(evidence) != {"schema_version", "platform", "task_repository_revision", "images"}
        or evidence.get("schema_version") != 1
        or evidence.get("platform") != PLATFORM
        or evidence.get("task_repository_revision") != TASK_REPOSITORY_REVISION
    ):
        raise PreparationError("base image evidence identity is invalid")
    raw_images = evidence.get("images")
    if not isinstance(raw_images, list) or len(raw_images) != len(instance_ids):
        raise PreparationError("base image evidence must contain one image per task")
    images: dict[str, Mapping[str, Any]] = {}
    for value in raw_images:
        image = _mapping(value, "base image")
        _exact_keys(image, _BASE_IMAGE_KEYS, "base image")
        instance_id = image.get("instance_id")
        if instance_id not in instance_ids or instance_id in images:
            raise PreparationError("base image evidence set does not match qualification")
        _validate_image_reference(image.get("image_reference"), image.get("image_digest"))
        if not _is_sha256(image.get("dockerfile_sha256")):
            raise PreparationError("base image Dockerfile fingerprint is invalid")
        images[cast(str, instance_id)] = image
    if list(images) != list(instance_ids):
        raise PreparationError("base image evidence ordering does not match qualification")
    return images


def _task_materials(
    task_repository: Path, record: Mapping[str, Any], image: Mapping[str, Any]
) -> dict[str, bytes]:
    instance_id = cast(str, record["instance_id"])
    task_dir = task_repository / "tasks" / instance_id
    if task_dir.is_symlink() or not task_dir.is_dir():
        raise PreparationError("official task directory is missing or unsafe")
    names = ("task.yaml", "Dockerfile", "eval.sh", "test.patch", "tests.json")
    materials = {name: _regular_file(task_dir / name, f"official {name}") for name in names}
    if _sha256(materials["Dockerfile"]) != image.get("dockerfile_sha256"):
        raise PreparationError("official task Dockerfile fingerprint drifted")
    try:
        metadata = _mapping(yaml.safe_load(materials["task.yaml"]), "official task metadata")
        tests = _mapping(json.loads(materials["tests.json"]), "official task tests")
    except (UnicodeDecodeError, yaml.YAMLError, json.JSONDecodeError) as exc:
        raise PreparationError("official task metadata is invalid") from exc
    expected_metadata = {
        "instance_id": record.get("instance_id"),
        "repo": record.get("repo"),
        "base_commit": record.get("base_commit"),
        "environment_setup_commit": record.get("environment_setup_commit"),
        "eval_type": record.get("eval_type"),
        "image": record.get("image"),
        "log_parser": record.get("log_parser"),
        "version": record.get("version"),
        "split": "test",
    }
    if any(metadata.get(key) != value for key, value in expected_metadata.items()):
        raise PreparationError("official task metadata does not match the frozen record")
    image_reference, _image_digest = _validate_image_reference(
        image.get("image_reference"), image.get("image_digest")
    )
    if _image_repository(image_reference) != _image_repository(metadata.get("image")):
        raise PreparationError("base task image provenance does not match frozen task.yaml image")
    datasets = metadata.get("datasets")
    if not isinstance(datasets, list) or "SWE-bench/SWE-bench" not in datasets:
        raise PreparationError("official task metadata dataset is invalid")
    if tests != {
        "FAIL_TO_PASS": record.get("FAIL_TO_PASS"),
        "PASS_TO_PASS": record.get("PASS_TO_PASS"),
    }:
        raise PreparationError("official task tests do not match the frozen record")
    for filename, field in (("eval.sh", "eval_script"), ("test.patch", "test_patch")):
        expected = record.get(field)
        if not isinstance(expected, str) or materials[filename] != expected.encode():
            raise PreparationError(f"official {filename} does not match the frozen record")
    dockerfile = materials["Dockerfile"].decode("utf-8", errors="strict")
    first_from = next(
        (
            line.strip()
            for line in dockerfile.splitlines()
            if line.strip().upper().startswith("FROM ")
        ),
        "",
    )
    if "--platform=linux/amd64" not in first_from or "arm64" in first_from:
        raise PreparationError("official task Dockerfile is not the pinned x86_64 recipe")
    return materials


def _write_file(path: Path, data: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _tree_sha256(context: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(context.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise PreparationError("prepared context contains a symbolic link")
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise PreparationError("prepared context is not owner-only")
        if path.is_dir():
            continue
        if not path.is_file():
            raise PreparationError("prepared context contains an unsupported file")
        relative = path.relative_to(context).as_posix()
        digest.update(f"file\0{relative}\0{stat.S_IMODE(path.stat().st_mode):04o}\0".encode())
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _content_set_sha256(materials: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(materials):
        digest.update(f"file\0{name}\0".encode())
        digest.update(materials[name])
        digest.update(b"\0")
    return digest.hexdigest()


def _host_test_spec_fields(
    *,
    record: Mapping[str, Any],
    task_metadata: Mapping[str, Any],
    tests: Mapping[str, Any],
    materials: Mapping[str, bytes],
) -> dict[str, object]:
    """Return the exact ten raw fields consumed by pinned ``make_test_spec``."""
    try:
        eval_script = materials["eval.sh"].decode("utf-8", errors="strict")
    except (KeyError, UnicodeDecodeError) as exc:
        raise PreparationError("official eval script is not valid UTF-8") from exc
    fields = {
        "instance_id": task_metadata.get("instance_id"),
        "image": task_metadata.get("image"),
        "repo": task_metadata.get("repo"),
        "version": task_metadata.get("version"),
        "FAIL_TO_PASS": tests.get("FAIL_TO_PASS"),
        "PASS_TO_PASS": tests.get("PASS_TO_PASS"),
        "log_parser": task_metadata.get("log_parser"),
        "eval_type": task_metadata.get("eval_type"),
        "eval_script": eval_script,
        "image_assets": task_metadata.get("image_assets"),
    }
    if set(fields) != _HOST_TEST_SPEC_FIELDS or fields["instance_id"] != record.get("instance_id"):
        raise PreparationError("official TestSpec identity is incomplete")
    for key in ("instance_id", "image", "repo", "version", "log_parser", "eval_type"):
        if not isinstance(fields[key], str) or not fields[key].strip():
            raise PreparationError(f"official TestSpec field {key} is invalid")
    for key in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        values = fields[key]
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise PreparationError(f"official TestSpec field {key} is invalid")
    return fields


def _execution_eval_script(canonical_eval_script: bytes) -> bytes:
    """Preserve the captured test status after the official cleanup command."""
    try:
        text = canonical_eval_script.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PreparationError("generated TestSpec eval script is not valid UTF-8") from exc
    if not canonical_eval_script.endswith(b"\n"):
        raise PreparationError("generated TestSpec eval script must end with a newline")
    lines = text.splitlines()
    capture_line = f"{TEST_EXIT_VARIABLE}=$?"
    if (
        lines.count(capture_line) != 1
        or lines.count(TEST_END_MARKER_LINE) != 1
        or lines.count(TEST_EXIT_MARKER_LINE) != 1
    ):
        raise PreparationError(
            "generated TestSpec eval script lacks one unambiguous test exit marker"
        )
    capture_index = lines.index(capture_line)
    end_index = lines.index(TEST_END_MARKER_LINE)
    marker_index = lines.index(TEST_EXIT_MARKER_LINE)
    if end_index != capture_index + 1 or marker_index != end_index + 1:
        raise PreparationError("generated TestSpec test exit marker ordering is unexpected")
    return canonical_eval_script + f'exit "${TEST_EXIT_VARIABLE}"\n'.encode("ascii")


def _host_test_spec_document(
    *,
    record: Mapping[str, Any],
    fields: Mapping[str, Any],
    materials: Mapping[str, bytes],
    source_record_sha256: str,
    trusted_tests_sha256: str,
    canonical_eval_script: bytes,
    scorer_entrypoint_sha256: str,
) -> dict[str, object]:
    """Freeze separate source, canonical generated, and execution-script identities."""
    if (
        not _is_sha256(source_record_sha256)
        or not _is_sha256(trusted_tests_sha256)
        or not _is_sha256(scorer_entrypoint_sha256)
        or fields.get("instance_id") != record.get("instance_id")
    ):
        raise PreparationError("host TestSpec provenance fingerprints are invalid")
    source_eval_script_sha256 = _sha256(materials["eval.sh"])
    test_spec_sha256 = _sha256(_canonical(fields))
    canonical_eval_script_sha256 = _sha256(canonical_eval_script)
    execution_eval_script = _execution_eval_script(canonical_eval_script)
    execution_eval_script_sha256 = _sha256(execution_eval_script)
    if not canonical_eval_script:
        raise PreparationError("generated TestSpec eval script is empty")
    return {
        "schema_version": 1,
        "swebench_version": SWEBENCH_VERSION,
        "instance_id": record["instance_id"],
        "source_record_sha256": source_record_sha256,
        "trusted_tests_sha256": trusted_tests_sha256,
        "source_eval_script_sha256": source_eval_script_sha256,
        "test_spec_sha256": test_spec_sha256,
        "canonical_eval_script_b64": base64.b64encode(canonical_eval_script).decode("ascii"),
        "canonical_eval_script_sha256": canonical_eval_script_sha256,
        "execution_eval_script_b64": base64.b64encode(execution_eval_script).decode("ascii"),
        "execution_eval_script_sha256": execution_eval_script_sha256,
        "scorer_entrypoint_sha256": scorer_entrypoint_sha256,
        "test_spec": dict(fields),
    }


def _validate_resource_limits(value: Mapping[str, Any]) -> dict[str, object]:
    _exact_keys(value, _SPEC_LIMIT_KEYS, "resource limits")
    for key in _SPEC_LIMIT_KEYS - {"cpus"}:
        item = value[key]
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise PreparationError(f"resource limit {key} must be a positive integer")
    cpus = value["cpus"]
    if not isinstance(cpus, str) or re.fullmatch(r"[1-9][0-9]*(?:\.[0-9]+)?", cpus) is None:
        raise PreparationError("resource limit cpus must be a positive decimal string")
    return dict(value)


def _default_control_runner(
    args: Sequence[str], input_bytes: bytes | None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 - fixed Docker argv and validated immutable image
        args,
        input=input_bytes,
        capture_output=True,
        timeout=120,
        check=False,
    )


def _run_control(
    runner: ControlRunner,
    args: Sequence[str],
    input_bytes: bytes | None,
    *,
    label: str,
) -> bytes:
    try:
        result = runner(tuple(args), input_bytes)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreparationError(f"{label} failed closed") from exc
    if result.returncode != 0 or len(result.stdout) > 1_000_000:
        raise PreparationError(f"{label} failed closed")
    return result.stdout


def _inspect_control_image(
    *,
    docker_executable: Path,
    control_image_reference: str,
    control_image_digest: str,
    runner: ControlRunner,
) -> None:
    raw = _run_control(
        runner,
        (
            str(docker_executable),
            "image",
            "inspect",
            f"--platform={CONTROL_PLATFORM}",
            control_image_reference,
        ),
        None,
        label="control image inspection",
    )
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreparationError("control image inspection evidence is invalid") from exc
    if not isinstance(payload, list) or len(payload) != 1:
        raise PreparationError("control image inspection evidence is invalid")
    image = _mapping(payload[0], "control image inspection")
    repo_digests = image.get("RepoDigests")
    if (
        not isinstance(image.get("Id"), str)
        or _IMAGE_DIGEST_RE.fullmatch(cast(str, image.get("Id"))) is None
        or not isinstance(repo_digests, list)
        or any(not isinstance(item, str) for item in repo_digests)
        or control_image_reference not in repo_digests
        or image.get("Os") != "linux"
        or image.get("Architecture") not in {"arm64", "aarch64"}
        or image.get("Variant") not in {None, "v8"}
        or not control_image_reference.endswith(f"@{control_image_digest}")
    ):
        raise PreparationError(
            "control image immutable digest or linux/arm64/v8 evidence is invalid"
        )


def _prepare_official_test_spec(
    *,
    instance_id: str,
    fields: Mapping[str, Any],
    control_image_reference: str,
    docker_executable: Path,
    resource_limits: Mapping[str, object],
    runner: ControlRunner,
) -> bytes:
    request = _canonical(
        {
            "schema_version": 1,
            "action": "prepare",
            "swebench_version": SWEBENCH_VERSION,
            "instance_id": instance_id,
            "test_spec": dict(fields),
        }
    )
    nofile = cast(int, resource_limits["nofile_limit"])
    args = (
        str(docker_executable),
        "run",
        "--rm",
        "--pull=never",
        f"--platform={CONTROL_PLATFORM}",
        "--network=none",
        "--read-only",
        "--user=65532:65532",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--pids-limit={resource_limits['pids_limit']}",
        f"--memory={resource_limits['memory_bytes']}",
        f"--cpus={resource_limits['cpus']}",
        f"--ulimit=nofile={nofile}:{nofile}",
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,nodev,size={resource_limits['tmpfs_bytes']}",  # noqa: S108 - container tmpfs
        "--interactive",
        "--entrypoint=python3",
        control_image_reference,
        "-B",
        SCORER_ENTRYPOINT_PATH,
    )
    raw = _run_control(
        runner,
        args,
        request,
        label="control image TestSpec preparation",
    )
    try:
        response = _mapping(json.loads(raw), "control image prepare response")
        _exact_keys(response, _PREPARE_RESPONSE_KEYS, "control image prepare response")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreparationError("control image prepare response is invalid") from exc
    expected_test_spec_sha256 = _sha256(_canonical(fields))
    if (
        response.get("schema_version") != 1
        or response.get("action") != "prepare"
        or response.get("scorer_status") != "prepared"
        or response.get("error") is not None
        or response.get("swebench_version") != SWEBENCH_VERSION
        or response.get("instance_id") != instance_id
        or response.get("test_spec_sha256") != expected_test_spec_sha256
        or response.get("scorer_entrypoint_sha256") != SCORER_ENTRYPOINT_SHA256
        or any(
            response.get(key) is not None
            for key in (
                "patch_sha256",
                "grading_log_sha256",
                "host_cleanup_confirmed",
                "resolved",
                "report",
            )
        )
    ):
        raise PreparationError("control image prepare response does not match frozen inputs")
    encoded_script = response.get("eval_script_b64")
    script_sha256 = response.get("eval_script_sha256")
    if not isinstance(encoded_script, str) or not _is_sha256(script_sha256):
        raise PreparationError("control image generated eval script fingerprint is invalid")
    try:
        canonical_eval_script = base64.b64decode(encoded_script, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PreparationError("control image generated eval script encoding is invalid") from exc
    if (
        base64.b64encode(canonical_eval_script).decode("ascii") != encoded_script
        or _sha256(canonical_eval_script) != script_sha256
        or not canonical_eval_script
    ):
        raise PreparationError(
            "control image generated eval script fingerprint does not match bytes"
        )
    return canonical_eval_script


def _image_entry(
    *,
    instance_id: str,
    base_commit: str,
    source_record_sha256: str,
    role: str,
    context: Path,
    scorer_sha256: str,
    trusted_tests_sha256: str,
    scope: str = "qualification",
    host_test_spec_path: str | None = None,
    host_test_spec_sha256: str | None = None,
) -> dict[str, object]:
    suffix = _sha256(f"{instance_id}\0{role}".encode())[:16]
    return {
        "instance_id": instance_id,
        "base_commit": base_commit,
        "source_record_sha256": source_record_sha256,
        "role": role,
        "tag": f"local/racecraft-swebench-{suffix}-{role}:{scope}",
        "context": str(context),
        "dockerfile": "Dockerfile",
        "context_sha256": _tree_sha256(context),
        "official_scorer_revision_sha256": scorer_sha256 if role == "grader" else None,
        "trusted_tests_sha256": trusted_tests_sha256 if role == "grader" else None,
        "host_test_spec_path": host_test_spec_path if role == "grader" else None,
        "host_test_spec_sha256": host_test_spec_sha256 if role == "grader" else None,
    }


def prepare_qualification_contexts(
    *,
    repo_root: Path,
    output_root: Path,
    verified_manifest: Path,
    verified_manifest_sha256: str,
    task_repository: Path,
    dataset_check_evidence: Path,
    dataset_check_evidence_sha256: str,
    base_image_evidence: Path,
    base_image_evidence_sha256: str,
    license_authorization_receipt: Path,
    grader_entrypoint: Path,
    grader_entrypoint_sha256: str,
    control_image_reference: str,
    control_image_digest: str,
    resource_limits: Mapping[str, Any],
    scope: str = "qualification",
    qualification_manifest: Path | None = None,
    qualification_manifest_sha256: str | None = None,
    qualification_records: Path | None = None,
    verified_records: Path | None = None,
    prior_qualification_manifest: Path | None = None,
    prior_qualification_exclusions: Path | None = None,
    revision_reader: RevisionReader = _default_revision_reader,
    docker_executable: Path = Path("docker"),
    control_runner: ControlRunner = _default_control_runner,
) -> dict[str, object]:
    """Validate frozen inputs and atomically emit private per-scope image contexts."""

    if scope not in SCOPES:
        raise PreparationError("scope must be qualification or verified")
    qualification_inputs = (
        qualification_manifest,
        qualification_manifest_sha256,
        qualification_records,
    )
    if scope == "qualification":
        if any(value is None for value in qualification_inputs) or verified_records is not None:
            raise PreparationError(
                "qualification scope requires qualification manifest, fingerprint, and records"
            )
    elif (
        verified_records is None
        or any(value is not None for value in qualification_inputs)
        or prior_qualification_manifest is not None
        or prior_qualification_exclusions is not None
    ):
        raise PreparationError(
            "verified scope requires Verified records and refuses qualification inputs"
        )
    repo = repo_root.resolve(strict=True)
    output = _future_output(output_root, repo)
    verified_path = _resolve_outside_repo(verified_manifest, repo, "Verified manifest")
    if scope == "qualification":
        selection_path = _resolve_outside_repo(
            cast(Path, qualification_manifest), repo, "qualification manifest"
        )
        selection_sha256 = cast(str, qualification_manifest_sha256)
        records_path = _resolve_outside_repo(
            cast(Path, qualification_records), repo, "qualification records"
        )
    else:
        selection_path = verified_path
        selection_sha256 = verified_manifest_sha256
        records_path = _resolve_outside_repo(cast(Path, verified_records), repo, "Verified records")
    prior_manifest_path = (
        _resolve_outside_repo(prior_qualification_manifest, repo, "prior qualification manifest")
        if prior_qualification_manifest is not None
        else None
    )
    prior_exclusions_path = (
        _resolve_outside_repo(
            prior_qualification_exclusions, repo, "prior qualification exclusions"
        )
        if prior_qualification_exclusions is not None
        else None
    )
    task_repo = _resolve_outside_repo(task_repository, repo, "official task repository")
    if task_repo.is_symlink() or not task_repo.is_dir():
        raise PreparationError("official task repository must be a real directory")
    if revision_reader(task_repo) != TASK_REPOSITORY_REVISION:
        raise PreparationError("task repository revision is not the pinned official revision")

    verified = _json_file(verified_path, "Verified manifest", verified_manifest_sha256)
    if scope == "qualification":
        qualification = _json_file(selection_path, "qualification manifest", selection_sha256)
        tasks, records = _validate_selection(
            qualification,
            verified,
            records_path,
            license_authorization_receipt,
            prior_manifest_path,
            prior_exclusions_path,
        )
    else:
        tasks, records = _validate_verified_selection(
            verified, records_path, license_authorization_receipt
        )
    instance_ids = [cast(str, task["instance_id"]) for task in tasks]
    check = _json_file(
        dataset_check_evidence, "dataset check evidence", dataset_check_evidence_sha256
    )
    _validate_dataset_check(check, instance_ids)
    base_evidence = _json_file(
        base_image_evidence, "base image evidence", base_image_evidence_sha256
    )
    base_images = _base_images(base_evidence, instance_ids)
    grader_bytes = _private_file(grader_entrypoint, "grader entrypoint", grader_entrypoint_sha256)
    if not grader_bytes.startswith(b"#!"):
        raise PreparationError("grader entrypoint must be an executable script")
    scorer_entrypoint_bytes = _regular_file(
        repo / "sandbox" / "swebench" / "scorer_entrypoint.py",
        "trusted scorer entrypoint",
    )
    scorer_entrypoint_sha256 = _sha256(scorer_entrypoint_bytes)
    if scorer_entrypoint_sha256 != SCORER_ENTRYPOINT_SHA256:
        raise PreparationError("trusted scorer entrypoint differs from the pinned source")
    scorer_sha = _sha256(
        _canonical(
            {
                "swebench_version": SWEBENCH_VERSION,
                "swebench_source_revision": SWEBENCH_SOURCE_REVISION,
                "grader_entrypoint_sha256": grader_entrypoint_sha256,
                "scorer_entrypoint_sha256": scorer_entrypoint_sha256,
            }
        )
    )
    limits = _validate_resource_limits(resource_limits)
    _validate_image_reference(control_image_reference, control_image_digest)

    validated: list[tuple[Mapping[str, Any], Mapping[str, Any], dict[str, bytes]]] = []
    for task, record in zip(tasks, records, strict=True):
        image = base_images[cast(str, record["instance_id"])]
        validated.append((task, image, _task_materials(task_repo, record, image)))
    _inspect_control_image(
        docker_executable=docker_executable,
        control_image_reference=control_image_reference,
        control_image_digest=control_image_digest,
        runner=control_runner,
    )

    stage = output.parent / f".{output.name}-stage-{uuid.uuid4().hex}"
    stage.mkdir(mode=0o700)
    installed = False
    try:
        contexts = stage / "contexts"
        contexts.mkdir(mode=0o700)
        host_test_spec_dir = stage / "host-test-specs"
        host_test_spec_dir.mkdir(mode=0o700)
        images: list[dict[str, object]] = []
        for (task, image, materials), record in zip(validated, records, strict=True):
            instance_id = cast(str, record["instance_id"])
            base_reference = cast(str, image["image_reference"])
            task_context = contexts / f"{instance_id}-task"
            grader_context = contexts / f"{instance_id}-grader"
            task_context.mkdir(mode=0o700)
            grader_context.mkdir(mode=0o700)
            # The ownership and user steps come first after FROM in both roles so the task and
            # grader images built from one base share identical layers.
            task_dockerfile = (
                f"FROM --platform={PLATFORM} {base_reference}\n"
                f"{TESTBED_OWNERSHIP_STEP}\n"
                f"{TESTBED_USER_STEP}\n"
                "COPY --chown=65532:65532 task.json /opt/racecraft/task.json\n"
                f"{TESTBED_ACTIVATION_STEP}\n"
            ).encode()
            _write_file(task_context / "Dockerfile", task_dockerfile)
            _write_file(
                task_context / "task.json",
                _canonical(
                    {
                        "instance_id": instance_id,
                        "problem_statement": record["problem_statement"],
                    }
                )
                + b"\n",
            )
            grader_dockerfile = (
                f"FROM --platform={PLATFORM} {base_reference}\n"
                f"{TESTBED_OWNERSHIP_STEP}\n"
                f"{TESTBED_USER_STEP}\n"
                f"{TESTBED_BASELINE_STEP}\n"
                "COPY --chown=65532:65532 task.yaml eval.sh test.patch tests.json "
                "/opt/racecraft/grader/\n"
                f"COPY --chown=0:0 --chmod=0444 {GENERATED_EVAL_CONTEXT_NAME} "
                f"{GENERATED_EVAL_IMAGE_PATH}\n"
                "COPY --chown=65532:65532 racecraft-swebench-grade "
                "/usr/local/bin/racecraft-swebench-grade\n"
                "RUN chmod 0555 /usr/local/bin/racecraft-swebench-grade\n"
            ).encode()
            _write_file(grader_context / "Dockerfile", grader_dockerfile)
            for name in ("task.yaml", "eval.sh", "test.patch", "tests.json"):
                _write_file(grader_context / name, materials[name])
            _write_file(grader_context / "racecraft-swebench-grade", grader_bytes, 0o500)
            trusted_tests_sha = _content_set_sha256(
                {
                    name: materials[name]
                    for name in ("task.yaml", "eval.sh", "test.patch", "tests.json")
                }
            )
            try:
                task_metadata = _mapping(
                    yaml.safe_load(materials["task.yaml"]), "official task metadata"
                )
                tests = _mapping(json.loads(materials["tests.json"]), "official task tests")
            except (UnicodeDecodeError, yaml.YAMLError, json.JSONDecodeError) as exc:
                raise PreparationError("official TestSpec source material is invalid") from exc
            fields = _host_test_spec_fields(
                record=record,
                task_metadata=task_metadata,
                tests=tests,
                materials=materials,
            )
            canonical_eval_script = _prepare_official_test_spec(
                instance_id=instance_id,
                fields=fields,
                control_image_reference=control_image_reference,
                docker_executable=docker_executable,
                resource_limits=limits,
                runner=control_runner,
            )
            test_spec_document = _host_test_spec_document(
                record=record,
                fields=fields,
                materials=materials,
                source_record_sha256=cast(str, task["record_sha256"]),
                trusted_tests_sha256=trusted_tests_sha,
                canonical_eval_script=canonical_eval_script,
                scorer_entrypoint_sha256=scorer_entrypoint_sha256,
            )
            test_spec_bytes = _canonical(test_spec_document) + b"\n"
            test_spec_sha = _sha256(test_spec_bytes)
            _write_file(host_test_spec_dir / f"{instance_id}.json", test_spec_bytes)
            execution_eval_script = base64.b64decode(
                cast(str, test_spec_document["execution_eval_script_b64"]), validate=True
            )
            _write_file(grader_context / GENERATED_EVAL_CONTEXT_NAME, execution_eval_script)
            common = {
                "instance_id": instance_id,
                "base_commit": cast(str, task["base_commit"]),
                "source_record_sha256": cast(str, task["record_sha256"]),
                "scorer_sha256": scorer_sha,
                "trusted_tests_sha256": trusted_tests_sha,
                "scope": scope,
            }
            task_image = _image_entry(role="task", context=task_context, **common)
            grader_image = _image_entry(
                role="grader",
                context=grader_context,
                host_test_spec_path=str(output / "host-test-specs" / f"{instance_id}.json"),
                host_test_spec_sha256=test_spec_sha,
                **common,
            )
            task_image["context"] = str(output / "contexts" / task_context.name)
            grader_image["context"] = str(output / "contexts" / grader_context.name)
            images.extend((task_image, grader_image))
        spec = {
            "schema_version": 1,
            "scope": scope,
            "platform": PLATFORM,
            "instance_ids": instance_ids,
            "selection_manifest_path": str(selection_path),
            "selection_manifest_sha256": selection_sha256,
            "resource_limits": limits,
            "control_image_reference": control_image_reference,
            "control_image_digest": control_image_digest,
            "images": images,
        }
        spec_bytes = _canonical(spec) + b"\n"
        _write_file(stage / "build-spec.json", spec_bytes)
        provenance: dict[str, object] = {
            "schema_version": 1,
            "scope": scope,
            "evidence_class": (
                "qualification_only_non_capability"
                if scope == "qualification"
                else "held_out_capability"
            ),
            "platform": PLATFORM,
            "emulation_required_on_apple_silicon": True,
            "task_count": len(records),
            "image_count": len(records) * 2,
            "verified_manifest_sha256": verified_manifest_sha256,
            "task_repository_revision": TASK_REPOSITORY_REVISION,
            "dataset_check_evidence_sha256": dataset_check_evidence_sha256,
            "base_image_evidence_sha256": base_image_evidence_sha256,
            "grader_entrypoint_sha256": grader_entrypoint_sha256,
            "scorer_entrypoint_sha256": scorer_entrypoint_sha256,
            "control_image_reference": control_image_reference,
            "control_image_digest": control_image_digest,
            "control_platform": CONTROL_PLATFORM,
            "official_scorer_revision_sha256": scorer_sha,
            "build_spec_sha256": _sha256(spec_bytes),
        }
        if scope == "qualification":
            provenance["qualification_manifest_sha256"] = selection_sha256
        _write_file(stage / "provenance.json", _canonical(provenance) + b"\n")
        os.replace(stage, output)
        installed = True
    finally:
        if not installed and stage.exists():
            shutil.rmtree(stage)
    return {
        "schema_version": 1,
        "scope": scope,
        "platform": PLATFORM,
        "task_count": len(records),
        "image_count": len(records) * 2,
        "task_repository_revision": TASK_REPOSITORY_REVISION,
        "build_spec_sha256": _sha256(spec_bytes),
        "provenance_sha256": _sha256((output / "provenance.json").read_bytes()),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--scope", choices=SCOPES, default="qualification")
    parser.add_argument("--qualification-manifest", type=Path)
    parser.add_argument("--qualification-manifest-sha256")
    parser.add_argument("--qualification-records", type=Path)
    parser.add_argument("--verified-manifest", type=Path, required=True)
    parser.add_argument("--verified-manifest-sha256", required=True)
    parser.add_argument("--verified-records", type=Path)
    parser.add_argument("--prior-qualification-manifest", type=Path)
    parser.add_argument("--prior-qualification-exclusions", type=Path)
    parser.add_argument("--task-repository", type=Path, required=True)
    parser.add_argument("--dataset-check-evidence", type=Path, required=True)
    parser.add_argument("--dataset-check-evidence-sha256", required=True)
    parser.add_argument("--base-image-evidence", type=Path, required=True)
    parser.add_argument("--base-image-evidence-sha256", required=True)
    parser.add_argument("--license-authorization-receipt", type=Path, required=True)
    parser.add_argument("--grader-entrypoint", type=Path, required=True)
    parser.add_argument("--grader-entrypoint-sha256", required=True)
    parser.add_argument("--control-image-reference", required=True)
    parser.add_argument("--control-image-digest", required=True)
    parser.add_argument("--memory-bytes", type=int, required=True)
    parser.add_argument("--cpus", required=True)
    parser.add_argument("--pids-limit", type=int, required=True)
    parser.add_argument("--nofile-limit", type=int, required=True)
    parser.add_argument("--tmpfs-bytes", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = prepare_qualification_contexts(
            repo_root=args.repo_root,
            output_root=args.output_root,
            scope=args.scope,
            qualification_manifest=args.qualification_manifest,
            qualification_manifest_sha256=args.qualification_manifest_sha256,
            qualification_records=args.qualification_records,
            verified_manifest=args.verified_manifest,
            verified_manifest_sha256=args.verified_manifest_sha256,
            verified_records=args.verified_records,
            prior_qualification_manifest=args.prior_qualification_manifest,
            prior_qualification_exclusions=args.prior_qualification_exclusions,
            task_repository=args.task_repository,
            dataset_check_evidence=args.dataset_check_evidence,
            dataset_check_evidence_sha256=args.dataset_check_evidence_sha256,
            base_image_evidence=args.base_image_evidence,
            base_image_evidence_sha256=args.base_image_evidence_sha256,
            license_authorization_receipt=args.license_authorization_receipt,
            grader_entrypoint=args.grader_entrypoint,
            grader_entrypoint_sha256=args.grader_entrypoint_sha256,
            control_image_reference=args.control_image_reference,
            control_image_digest=args.control_image_digest,
            resource_limits={
                "memory_bytes": args.memory_bytes,
                "cpus": args.cpus,
                "pids_limit": args.pids_limit,
                "nofile_limit": args.nofile_limit,
                "tmpfs_bytes": args.tmpfs_bytes,
            },
        )
    except (OSError, PreparationError, ValueError) as exc:
        raise SystemExit(f"{args.scope} context preparation failed: {exc}") from exc
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
