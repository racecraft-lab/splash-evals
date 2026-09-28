#!/usr/bin/env python3
"""Build and attest SWE-bench images from frozen private contexts.

This entrypoint accepts synthetic, qualification (at most ten tasks), or verified
(exactly the ordered Verified 500) scopes. Building Verified images does not
approve a Verified run; the harness still requires its explicit full-run approval.
Build contexts and the resulting attestation stay outside Git; stdout contains
only counts and fingerprints.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import yaml

CONTROL_PLATFORM = "linux/arm64/v8"
# The official task and grader bases are frozen as linux/amd64; the local
# control image is separately pinned and inspected as linux/arm64/v8.
QUALIFICATION_PLATFORM = "linux/amd64"
SYNTHETIC_PLATFORM = "linux/arm64/v8"
SCORER_ENTRYPOINT_SHA256 = "e313d7fdf0ff500562cddaccd4feada8d9cba4c8b4adf838685fd893e0896858"
GENERATED_EVAL_CONTEXT_NAME = "test-spec-eval.sh"
GENERATED_EVAL_IMAGE_PATH = "/opt/racecraft/grader/test-spec-eval.sh"
TEST_EXIT_VARIABLE = "SWEBENCH_TEST_EXIT_CODE"
TEST_END_MARKER_LINE = ": '>>>>> End Test Output'"
TEST_EXIT_MARKER_LINE = 'echo ">>>>> Test Exit Code: $SWEBENCH_TEST_EXIT_CODE"'
MAX_QUALIFICATION_TASKS = 10
VERIFIED_TASK_COUNT = 500
# Official SWE-bench scopes use frozen linux/amd64 task/grader bases and host TestSpecs.
_OFFICIAL_SCOPES = {"qualification", "verified"}
# Image label binding a locally built image to its exact frozen build inputs.
BUILD_INPUTS_LABEL = "org.racecraft.swebench.build-inputs-sha256"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}")
_REVISION_RE = re.compile(r"[0-9a-f]{40}")
_INSTANCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}")
_TAG_RE = re.compile(r"[a-z0-9][a-z0-9._/-]{0,199}:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RUNTIME_ENV_OVERRIDES = {
    "HOME": "/tmp/home",  # noqa: S108 - container-only HOME override
    "PATH": "/usr/local/bin:/usr/bin:/bin",
}
_SPEC_KEYS = {
    "schema_version",
    "scope",
    "platform",
    "instance_ids",
    "selection_manifest_path",
    "selection_manifest_sha256",
    "resource_limits",
    "control_image_reference",
    "control_image_digest",
    "images",
}
_LIMIT_KEYS = {
    "memory_bytes",
    "cpus",
    "pids_limit",
    "nofile_limit",
    "tmpfs_bytes",
}
_IMAGE_KEYS = {
    "instance_id",
    "base_commit",
    "source_record_sha256",
    "role",
    "tag",
    "context",
    "dockerfile",
    "context_sha256",
    "official_scorer_revision_sha256",
    "trusted_tests_sha256",
    "host_test_spec_path",
    "host_test_spec_sha256",
}
_HOST_TEST_SPEC_KEYS = {
    "schema_version",
    "swebench_version",
    "instance_id",
    "source_record_sha256",
    "trusted_tests_sha256",
    "source_eval_script_sha256",
    "test_spec_sha256",
    "canonical_eval_script_b64",
    "canonical_eval_script_sha256",
    "execution_eval_script_b64",
    "execution_eval_script_sha256",
    "scorer_entrypoint_sha256",
    "test_spec",
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
_ISOLATION_CONTRACT = {
    "schema_version": 1,
    "control_platform": CONTROL_PLATFORM,
    "build_network": "none",
    "pull": False,
    "runtime_network": "none",
    "runtime_user": "65532:65532",
    "read_only": True,
    "no_new_privileges": True,
    "cap_drop": ["ALL"],
    "mounts": [],
    "ports": [],
    "cleanup_verified": True,
    "runtime_environment": {
        "source": "immutable_base_image",
        "overrides": _RUNTIME_ENV_OVERRIDES,
        "unique_names": True,
    },
}


class BuildError(ValueError):
    """Raised when frozen inputs or Docker evidence violate the contract."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _execution_platform(scope: object) -> str:
    return QUALIFICATION_PLATFORM if scope in _OFFICIAL_SCOPES else SYNTHETIC_PLATFORM


def _isolation_revision_sha256(execution_platform: str) -> str:
    return _sha256(_canonical({**_ISOLATION_CONTRACT, "execution_platform": execution_platform}))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BuildError(f"{label} must be an object")
    return cast(Mapping[str, Any], value)


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise BuildError(f"{label} fields do not match the frozen schema")


def _private_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise BuildError(f"{label} must be a regular file")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise BuildError(f"{label} must be owner-only")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise BuildError(f"{label} is unreadable") from exc


def _tree_sha256(context: Path) -> str:
    digest = hashlib.sha256()
    found_file = False
    for path in sorted(context.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise BuildError("build contexts may not contain symbolic links")
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise BuildError("build contexts must remain owner-only")
        if path.is_dir():
            continue
        if not path.is_file():
            raise BuildError("build contexts may contain only directories and regular files")
        found_file = True
        relative = path.relative_to(context).as_posix()
        mode = stat.S_IMODE(path.stat().st_mode)
        digest.update(f"file\0{relative}\0{mode:04o}\0".encode())
        digest.update(path.read_bytes())
        digest.update(b"\0")
    if not found_file:
        raise BuildError("build context must not be empty")
    return digest.hexdigest()


def _content_set_sha256(materials: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(materials):
        digest.update(f"file\0{name}\0".encode())
        digest.update(materials[name])
        digest.update(b"\0")
    return digest.hexdigest()


def _host_test_spec_sha256(fields: Mapping[str, Any]) -> str:
    payload = json.dumps(
        fields,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return _sha256(payload)


def _execution_eval_script(canonical_eval_script: bytes) -> bytes:
    try:
        text = canonical_eval_script.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise BuildError("generated TestSpec eval script is not valid UTF-8") from exc
    if not canonical_eval_script.endswith(b"\n"):
        raise BuildError("generated TestSpec eval script must end with a newline")
    lines = text.splitlines()
    capture_line = f"{TEST_EXIT_VARIABLE}=$?"
    if (
        lines.count(capture_line) != 1
        or lines.count(TEST_END_MARKER_LINE) != 1
        or lines.count(TEST_EXIT_MARKER_LINE) != 1
    ):
        raise BuildError("generated TestSpec eval script lacks one unambiguous test exit marker")
    capture_index = lines.index(capture_line)
    end_index = lines.index(TEST_END_MARKER_LINE)
    marker_index = lines.index(TEST_EXIT_MARKER_LINE)
    if end_index != capture_index + 1 or marker_index != end_index + 1:
        raise BuildError("generated TestSpec test exit marker ordering is unexpected")
    return canonical_eval_script + f'exit "${TEST_EXIT_VARIABLE}"\n'.encode("ascii")


def _validate_host_test_spec(
    *,
    repo: Path,
    image: Mapping[str, Any],
    context: Path,
    instance_id: str,
) -> tuple[bytes | None, str | None, Mapping[str, Any] | None]:
    spec_path_value = image["host_test_spec_path"]
    spec_sha_value = image["host_test_spec_sha256"]
    if spec_path_value is None and spec_sha_value is None:
        return None, None, None
    if not isinstance(spec_path_value, str) or not isinstance(spec_sha_value, str):
        raise BuildError("host TestSpec path and fingerprint must be bound together")
    if _SHA256_RE.fullmatch(spec_sha_value) is None:
        raise BuildError("host TestSpec fingerprint is invalid")
    spec_path = _outside_repo(Path(spec_path_value), repo, "host TestSpec")
    if spec_path.is_relative_to(context):
        raise BuildError("host TestSpec may not be included in an image context")
    spec_raw = _private_file(spec_path, "host TestSpec")
    if _sha256(spec_raw) != spec_sha_value:
        raise BuildError("host TestSpec fingerprint does not match frozen bytes")
    try:
        document = _mapping(json.loads(spec_raw), "host TestSpec")
        _exact_keys(document, _HOST_TEST_SPEC_KEYS, "host TestSpec")
        fields = _mapping(document["test_spec"], "host TestSpec fields")
        _exact_keys(fields, _HOST_TEST_SPEC_FIELDS, "host TestSpec fields")
        metadata = _mapping(yaml.safe_load((context / "task.yaml").read_bytes()), "grader metadata")
        tests = _mapping(json.loads((context / "tests.json").read_bytes()), "grader tests")
        materials = {
            name: (context / name).read_bytes()
            for name in ("task.yaml", "eval.sh", "test.patch", "tests.json")
        }
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise BuildError("host TestSpec or grader materials are invalid") from exc
    expected_tests_sha = image["trusted_tests_sha256"]
    canonical_eval_script_b64 = document.get("canonical_eval_script_b64")
    execution_eval_script_b64 = document.get("execution_eval_script_b64")
    if not isinstance(canonical_eval_script_b64, str) or not isinstance(
        execution_eval_script_b64, str
    ):
        raise BuildError("host TestSpec generated eval script is invalid")
    try:
        canonical_eval_script = base64.b64decode(canonical_eval_script_b64, validate=True)
        execution_eval_script = base64.b64decode(execution_eval_script_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BuildError("host TestSpec generated eval script encoding is invalid") from exc
    expected_execution_script = _execution_eval_script(canonical_eval_script)
    scorer_entrypoint_path = repo / "sandbox" / "swebench" / "scorer_entrypoint.py"
    scorer_entrypoint_sha256 = (
        _sha256(scorer_entrypoint_path.read_bytes())
        if scorer_entrypoint_path.is_file() and not scorer_entrypoint_path.is_symlink()
        else None
    )
    if (
        document["schema_version"] != 1
        or document["swebench_version"] != "5.0.2"
        or document["instance_id"] != instance_id
        or document["source_record_sha256"] != image["source_record_sha256"]
        or document["trusted_tests_sha256"] != expected_tests_sha
        or document["source_eval_script_sha256"] != _sha256(materials["eval.sh"])
        or document["test_spec_sha256"] != _host_test_spec_sha256(fields)
        or document["canonical_eval_script_sha256"] != _sha256(canonical_eval_script)
        or document["execution_eval_script_sha256"] != _sha256(execution_eval_script)
        or execution_eval_script != expected_execution_script
        or base64.b64encode(canonical_eval_script).decode("ascii") != canonical_eval_script_b64
        or base64.b64encode(execution_eval_script).decode("ascii") != execution_eval_script_b64
        or document["scorer_entrypoint_sha256"] != SCORER_ENTRYPOINT_SHA256
        or scorer_entrypoint_sha256 != SCORER_ENTRYPOINT_SHA256
        or _content_set_sha256(materials) != expected_tests_sha
    ):
        raise BuildError("host TestSpec provenance does not match frozen grader materials")
    expected_fields = {
        "instance_id": metadata.get("instance_id"),
        "image": metadata.get("image"),
        "repo": metadata.get("repo"),
        "version": metadata.get("version"),
        "FAIL_TO_PASS": tests.get("FAIL_TO_PASS"),
        "PASS_TO_PASS": tests.get("PASS_TO_PASS"),
        "log_parser": metadata.get("log_parser"),
        "eval_type": metadata.get("eval_type"),
        "eval_script": materials["eval.sh"].decode("utf-8", errors="strict"),
        "image_assets": metadata.get("image_assets"),
    }
    if dict(fields) != expected_fields or fields["instance_id"] != instance_id:
        raise BuildError("host TestSpec fields differ from frozen grader materials")
    generated_eval_path = context / GENERATED_EVAL_CONTEXT_NAME
    if (
        generated_eval_path.is_symlink()
        or not generated_eval_path.is_file()
        or generated_eval_path.read_bytes() != execution_eval_script
    ):
        raise BuildError("grader context generated eval script differs from frozen bytes")
    return spec_raw, spec_sha_value, document


def _validate_dockerfile(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise BuildError("Dockerfile must be readable UTF-8") from exc
    from_lines = [line.strip() for line in lines if line.strip().upper().startswith("FROM ")]
    if not from_lines:
        raise BuildError("Dockerfile must declare an immutable base")
    for line in from_lines:
        words = line.split()
        image = next((word for word in words[1:] if not word.startswith("--")), "")
        if image != "scratch" and re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image) is None:
            raise BuildError("every Dockerfile base must be scratch or digest-pinned")
    if any(line.lstrip().upper().startswith("ADD ") for line in lines):
        raise BuildError("Dockerfile ADD is forbidden; use frozen local COPY inputs")


def _image_repository(reference: object) -> str | None:
    if not isinstance(reference, str) or not reference or any(c.isspace() for c in reference):
        return None
    name = reference.split("@", 1)[0]
    final_component = name.rsplit("/", 1)[-1]
    if ":" in final_component:
        name = name[: -len(final_component)] + final_component.rsplit(":", 1)[0]
    return name or None


def _qualification_base_reference(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise BuildError("qualification Dockerfile must be readable UTF-8") from exc
    from_lines = [line.strip() for line in lines if line.strip().upper().startswith("FROM ")]
    if len(from_lines) != 1:
        raise BuildError("qualification Dockerfile must use one immutable linux/amd64 base")
    words = from_lines[0].split()
    if (
        len(words) != 3
        or words[1] != f"--platform={QUALIFICATION_PLATFORM}"
        or words[2] == "scratch"
        or re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", words[2]) is None
    ):
        raise BuildError("qualification Dockerfile must use one immutable linux/amd64 base")
    return words[2]


def _outside_repo(path: Path, repo: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BuildError(f"{label} does not exist") from exc
    if resolved == repo or resolved.is_relative_to(repo):
        raise BuildError(f"{label} must remain outside the Git checkout")
    return resolved


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BuildError(f"{label} must be a positive integer")
    return value


def _validate_spec(repo: Path, spec_path: Path) -> tuple[Mapping[str, Any], str]:
    raw = _private_file(spec_path, "build spec")
    try:
        spec = _mapping(json.loads(raw), "build spec")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError("build spec is invalid JSON") from exc
    _exact_keys(spec, _SPEC_KEYS, "build spec")
    if spec["schema_version"] != 1:
        raise BuildError("build spec schema is unsupported")
    scope = spec["scope"]
    if scope not in {"synthetic", *_OFFICIAL_SCOPES}:
        raise BuildError("build scope must be synthetic, qualification, or verified")
    expected_platform = _execution_platform(scope)
    if spec["platform"] != expected_platform:
        raise BuildError(f"{scope} platform must be {expected_platform}")
    _validate_image_reference(
        spec["control_image_reference"], spec["control_image_digest"], "control image"
    )
    instance_ids = spec["instance_ids"]
    if not isinstance(instance_ids, list) or not instance_ids:
        raise BuildError("instance_ids must be a nonempty list")
    if scope == "verified":
        if len(instance_ids) != VERIFIED_TASK_COUNT:
            raise BuildError(f"verified builds must contain exactly {VERIFIED_TASK_COUNT} tasks")
    elif len(instance_ids) > MAX_QUALIFICATION_TASKS:
        raise BuildError("pre-approval builds may contain at most 10 tasks")
    if any(
        not isinstance(item, str) or _INSTANCE_RE.fullmatch(item) is None for item in instance_ids
    ):
        raise BuildError("instance_ids contain an unsafe value")
    if len(set(instance_ids)) != len(instance_ids):
        raise BuildError("instance_ids must be unique")

    limits = _mapping(spec["resource_limits"], "resource_limits")
    _exact_keys(limits, _LIMIT_KEYS, "resource_limits")
    _positive_int(limits["memory_bytes"], "memory_bytes")
    _positive_int(limits["pids_limit"], "pids_limit")
    _positive_int(limits["nofile_limit"], "nofile_limit")
    _positive_int(limits["tmpfs_bytes"], "tmpfs_bytes")
    try:
        cpus = float(limits["cpus"])
    except (TypeError, ValueError) as exc:
        raise BuildError("cpus must be a positive decimal") from exc
    if cpus <= 0 or cpus > 64:
        raise BuildError("cpus must be between 0 and 64")

    images = spec["images"]
    if not isinstance(images, list) or len(images) != len(instance_ids) * 2:
        raise BuildError("every instance must bind one task and one grader image")
    bindings: set[tuple[str, str]] = set()
    source_bindings: dict[str, tuple[str, str]] = {}
    context_owners: dict[Path, tuple[str, str]] = {}
    qualification_base_references: dict[tuple[str, str], str] = {}
    for index, raw_image in enumerate(images):
        image = _mapping(raw_image, f"images[{index}]")
        _exact_keys(image, _IMAGE_KEYS, f"images[{index}]")
        instance_id = image["instance_id"]
        if instance_id not in instance_ids:
            raise BuildError("image binding names an unknown instance")
        role = image["role"]
        binding = (cast(str, instance_id), cast(str, role))
        if role not in {"task", "grader"} or binding in bindings:
            raise BuildError("every instance must bind one task and one grader image")
        bindings.add(binding)
        base_commit = image["base_commit"]
        source_record_sha256 = image["source_record_sha256"]
        if (
            not isinstance(base_commit, str)
            or _REVISION_RE.fullmatch(base_commit) is None
            or not isinstance(source_record_sha256, str)
            or _SHA256_RE.fullmatch(source_record_sha256) is None
        ):
            raise BuildError("image source binding is invalid")
        source_binding = (base_commit, source_record_sha256)
        previous = source_bindings.setdefault(cast(str, instance_id), source_binding)
        if previous != source_binding:
            raise BuildError("task and grader must bind the same frozen source record")
        tag = image["tag"]
        if not isinstance(tag, str) or _TAG_RE.fullmatch(tag) is None or "@" in tag:
            raise BuildError("image tag is unsafe or mutable syntax is invalid")
        context = _outside_repo(Path(cast(str, image["context"])), repo, "build context")
        if context.is_symlink() or not context.is_dir():
            raise BuildError("build context must be a real directory")
        if stat.S_IMODE(context.stat().st_mode) & 0o077:
            raise BuildError("build context must remain owner-only")
        previous_context_owner = context_owners.setdefault(context, binding)
        if previous_context_owner != binding:
            raise BuildError("image build contexts must be distinct")
        dockerfile = image["dockerfile"]
        if (
            not isinstance(dockerfile, str)
            or not dockerfile
            or Path(dockerfile).is_absolute()
            or ".." in Path(dockerfile).parts
        ):
            raise BuildError("Dockerfile path must stay inside its build context")
        dockerfile_path = context / dockerfile
        if dockerfile_path.is_symlink() or not dockerfile_path.is_file():
            raise BuildError("Dockerfile must be a regular file inside its build context")
        if role == "task":
            task_context_entries = {
                path.relative_to(context).as_posix() for path in context.rglob("*")
            }
            task_json = context / "task.json"
            if (
                dockerfile != "Dockerfile"
                or task_context_entries != {"Dockerfile", "task.json"}
                or task_json.is_symlink()
                or not task_json.is_file()
            ):
                raise BuildError("task build context may contain only Dockerfile and task.json")
        _validate_dockerfile(dockerfile_path)
        if scope in _OFFICIAL_SCOPES:
            base_reference = _qualification_base_reference(dockerfile_path)
            qualification_base_references[binding] = base_reference
            if role == "grader":
                task_metadata_path = context / "task.yaml"
                if task_metadata_path.is_symlink() or not task_metadata_path.is_file():
                    raise BuildError("qualification grader task.yaml must be a regular file")
                try:
                    metadata = _mapping(
                        yaml.safe_load(task_metadata_path.read_bytes()),
                        "qualification grader metadata",
                    )
                except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
                    raise BuildError("qualification grader task.yaml is invalid") from exc
                if _image_repository(base_reference) != _image_repository(metadata.get("image")):
                    raise BuildError(
                        "qualification grader base image does not match frozen task.yaml image"
                    )
        expected_hash = image["context_sha256"]
        if not isinstance(expected_hash, str) or _SHA256_RE.fullmatch(expected_hash) is None:
            raise BuildError("context fingerprint must be an exact sha256")
        if _tree_sha256(context) != expected_hash:
            raise BuildError("context fingerprint does not match frozen bytes")
        scorer_revision = image["official_scorer_revision_sha256"]
        trusted_tests = image["trusted_tests_sha256"]
        host_spec_path = image["host_test_spec_path"]
        host_spec_sha = image["host_test_spec_sha256"]
        host_test_spec_document = None
        if host_spec_path is not None or host_spec_sha is not None:
            _spec_bytes, _spec_sha, host_test_spec_document = _validate_host_test_spec(
                repo=repo,
                image=image,
                context=context,
                instance_id=cast(str, instance_id),
            )
        if role == "task" and (scorer_revision is not None or trusted_tests is not None):
            raise BuildError("task images may not bind trusted grader material")
        if role == "task" and (host_spec_path is not None or host_spec_sha is not None):
            raise BuildError("task images may not bind host-only TestSpec material")
        if role == "grader" and (
            not isinstance(scorer_revision, str)
            or _SHA256_RE.fullmatch(scorer_revision) is None
            or not isinstance(trusted_tests, str)
            or _SHA256_RE.fullmatch(trusted_tests) is None
        ):
            raise BuildError("grader provenance must bind scorer and trusted tests")
        if role == "grader" and scope in _OFFICIAL_SCOPES:
            if host_test_spec_document is None:
                raise BuildError("qualification grader must bind a host-only TestSpec")
        if role == "grader" and host_test_spec_document is not None:
            grader_context_entries = {
                path.relative_to(context).as_posix() for path in context.rglob("*")
            }
            if grader_context_entries != {
                "Dockerfile",
                "eval.sh",
                "racecraft-swebench-grade",
                "task.yaml",
                "test-spec-eval.sh",
                "test.patch",
                "tests.json",
            }:
                raise BuildError("grader build context files differ from the frozen allowlist")
            expected_generated_copy = (
                "COPY --chown=0:0 --chmod=0444 "
                f"{GENERATED_EVAL_CONTEXT_NAME} {GENERATED_EVAL_IMAGE_PATH}"
            )
            try:
                dockerfile_text = dockerfile_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise BuildError("grader Dockerfile is invalid") from exc
            if dockerfile_text.splitlines().count(expected_generated_copy) != 1:
                raise BuildError(
                    "grader Dockerfile must bake the root-owned frozen execution script"
                )
    expected_bindings = {
        (cast(str, instance_id), role)
        for instance_id in instance_ids
        for role in ("task", "grader")
    }
    if bindings != expected_bindings:
        raise BuildError("every instance must bind one task and one grader image")
    if scope in _OFFICIAL_SCOPES:
        for instance_id in instance_ids:
            if qualification_base_references.get((instance_id, "task")) != (
                qualification_base_references.get((instance_id, "grader"))
            ):
                raise BuildError(
                    "qualification task and grader must use the same "
                    "immutable per-instance base image"
                )
    selection_path = _outside_repo(
        Path(cast(str, spec["selection_manifest_path"])), repo, "selection manifest"
    )
    selection_raw = _private_file(selection_path, "selection manifest")
    selection_sha = spec["selection_manifest_sha256"]
    if (
        not isinstance(selection_sha, str)
        or _SHA256_RE.fullmatch(selection_sha) is None
        or _sha256(selection_raw) != selection_sha
    ):
        raise BuildError("selection manifest fingerprint does not match frozen bytes")
    try:
        selection = _mapping(json.loads(selection_raw), "selection manifest")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError("selection manifest is invalid JSON") from exc
    tasks = selection.get("tasks")
    if not isinstance(tasks, list) or selection.get("task_count") != len(instance_ids):
        raise BuildError("selection manifest task count is invalid")
    selected_source = []
    for raw_task in tasks:
        task = _mapping(raw_task, "selection task")
        selected_source.append(
            (task.get("instance_id"), task.get("base_commit"), task.get("record_sha256"))
        )
    expected_source = [
        (instance_id, *source_bindings[cast(str, instance_id)]) for instance_id in instance_ids
    ]
    if selected_source != expected_source:
        raise BuildError("image bindings do not match the frozen selection manifest")
    if scope == "verified":
        policy = selection.get("selection_policy")
        if (
            selection.get("verified_500_instance_ids") != instance_ids
            or not isinstance(policy, Mapping)
            or policy.get("evidence_class") != "held_out_capability"
        ):
            raise BuildError("verified builds require the frozen ordered Verified manifest")
    return spec, _sha256(raw)


def _validate_image_reference(reference: object, digest: object, label: str) -> tuple[str, str]:
    if (
        not isinstance(reference, str)
        or not isinstance(digest, str)
        or _IMAGE_ID_RE.fullmatch(digest) is None
        or not reference.endswith(f"@{digest}")
        or reference.count("@") != 1
        or any(character.isspace() for character in reference)
    ):
        raise BuildError(f"{label} must bind one exact sha256 digest")
    return reference, digest


def _run(
    runner: Runner, args: Sequence[str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(tuple(args), check=check)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BuildError("Docker operation failed closed") from exc


def _default_runner(args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed Docker argv plus validated local paths/tags
        args,
        check=check,
        capture_output=True,
        text=True,
        timeout=3600,
    )


def _json_first(result: subprocess.CompletedProcess[str], label: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(result.stdout)
        item = payload[0]
    except (UnicodeDecodeError, json.JSONDecodeError, IndexError, TypeError) as exc:
        raise BuildError(f"{label} evidence is invalid") from exc
    return _mapping(item, label)


def _immutable_reference(tag: str, image_id: str) -> str:
    last_slash = tag.rfind("/")
    tag_separator = tag.rfind(":")
    repository = tag[:tag_separator] if tag_separator > last_slash else tag
    return f"{repository}@{image_id}"


def _attest_runtime(
    docker: str,
    runner: Runner,
    tag: str,
    image_id: str,
    name: str,
    limits: Mapping[str, Any],
    execution_platform: str,
) -> Mapping[str, Any]:
    cpus = str(limits["cpus"])
    nofile = cast(int, limits["nofile_limit"])
    args = (
        docker,
        "create",
        f"--name={name}",
        f"--platform={execution_platform}",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--pids-limit={limits['pids_limit']}",
        f"--memory={limits['memory_bytes']}",
        f"--cpus={cpus}",
        f"--ulimit=nofile={nofile}:{nofile}",
        f"--tmpfs=/tmp:rw,noexec,nosuid,nodev,size={limits['tmpfs_bytes']}",
        "--user=65532:65532",
        "--env=HOME=/tmp/home",
        "--env=PATH=/usr/local/bin:/usr/bin:/bin",
        image_id,
        "true",
    )
    created = False
    try:
        _run(runner, args)
        created = True
        inspected = _json_first(_run(runner, (docker, "inspect", name)), "container")
        config = _mapping(inspected.get("Config"), "container.Config")
        host = _mapping(inspected.get("HostConfig"), "container.HostConfig")
        mounts = inspected.get("Mounts")
        expected_ulimit = [{"Name": "nofile", "Soft": nofile, "Hard": nofile}]
        expected_tmpfs = {
            "/tmp": f"rw,noexec,nosuid,nodev,size={limits['tmpfs_bytes']}"  # noqa: S108 - container-only bounded tmpfs
        }
        safe = (
            inspected.get("Image") == image_id
            and config.get("Image") in {None, image_id}
            and config.get("User") == "65532:65532"
            and _runtime_environment_is_safe(config.get("Env"))
            and not config.get("ExposedPorts")
            and host.get("NetworkMode") == "none"
            and host.get("ReadonlyRootfs") is True
            and host.get("Privileged") is False
            and host.get("CapDrop") == ["ALL"]
            and "no-new-privileges" in host.get("SecurityOpt", [])
            and host.get("PidsLimit") == limits["pids_limit"]
            and host.get("Memory") == limits["memory_bytes"]
            and host.get("NanoCpus") == int(float(cpus) * 1_000_000_000)
            and host.get("Ulimits") == expected_ulimit
            and host.get("Tmpfs") == expected_tmpfs
            and not host.get("Binds")
            and not host.get("PortBindings")
            and not host.get("ExtraHosts")
            and not host.get("Devices")
            and not host.get("DeviceRequests")
            and host.get("IpcMode") in {None, "", "private"}
            and host.get("PidMode") in {None, ""}
            and host.get("UTSMode") in {None, ""}
            and mounts == []
        )
        if not safe:
            raise BuildError("container runtime safety contract did not attest")
        return {
            "user": "65532:65532",
            "network": "none",
            "read_only": True,
            "no_new_privileges": True,
            "capabilities_dropped": ["ALL"],
            "mounts": [],
            "ports": [],
            "memory_bytes": limits["memory_bytes"],
            "cpus": cpus,
            "pids_limit": limits["pids_limit"],
            "nofile_limit": nofile,
            "tmpfs_bytes": limits["tmpfs_bytes"],
        }
    finally:
        if created:
            _run(runner, (docker, "rm", "--force", name))
            remaining = _run(runner, (docker, "inspect", name), check=False)
            if remaining.returncode == 0:
                raise BuildError("temporary attestation container cleanup failed")


def _runtime_environment_is_safe(value: object) -> bool:
    if not isinstance(value, list):
        return False
    environment: dict[str, str] = {}
    for entry in value:
        if not isinstance(entry, str) or "=" not in entry:
            return False
        name, setting = entry.split("=", 1)
        if _ENV_NAME_RE.fullmatch(name) is None or name in environment:
            return False
        environment[name] = setting
    return all(environment.get(name) == setting for name, setting in _RUNTIME_ENV_OVERRIDES.items())


def _private_destination(state: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts or relative.name in {"", "."}:
        raise BuildError("private artifact path is unsafe")
    directory = state
    for part in relative.parent.parts:
        directory /= part
        if directory.exists():
            if directory.is_symlink() or not directory.is_dir():
                raise BuildError("private artifact directory is unsafe")
        else:
            directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
    destination = directory / relative.name
    if destination.is_symlink():
        raise BuildError("private artifact destination is unsafe")
    return destination


def _write_private_json(state: Path, relative: Path, evidence: Mapping[str, Any]) -> str:
    payload = _canonical(evidence) + b"\n"
    payload_sha = _sha256(payload)
    destination = _private_destination(state, relative)
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise BuildError(
            "attestation already exists; refusing to overwrite frozen evidence"
        ) from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return payload_sha


def _write_private_bytes(state: Path, relative: Path, payload: bytes) -> str:
    digest = _sha256(payload)
    destination = _private_destination(state, relative)
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise BuildError("private host TestSpec already exists; refusing to overwrite") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return digest


def _existing_image_matches(
    docker: str, runner: Runner, tag: str, execution_platform: str, context_sha256: str
) -> bool:
    """Return True only when the local tag carries this exact frozen build-input label."""
    result = _run(
        runner,
        (docker, "image", "inspect", f"--platform={execution_platform}", tag),
        check=False,
    )
    if result.returncode != 0:
        return False
    config = _json_first(result, "existing image").get("Config")
    labels = config.get("Labels") if isinstance(config, Mapping) else None
    return isinstance(labels, Mapping) and labels.get(BUILD_INPUTS_LABEL) == context_sha256


def build_images(
    repo_root: Path,
    state_root: Path,
    spec_path: Path,
    docker_executable: Path,
    runner: Runner = _default_runner,
    *,
    reuse_existing_images: bool = False,
) -> dict[str, object]:
    repo = repo_root.resolve(strict=True)
    state = state_root.resolve() if state_root.exists() else state_root.absolute()
    if state == repo or state.is_relative_to(repo):
        raise BuildError("private state must remain outside the Git checkout")
    if state.exists() and (state.is_symlink() or not state.is_dir()):
        raise BuildError("private state must be a real directory")
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    frozen_spec = _outside_repo(spec_path, repo, "build spec")
    spec, spec_sha = _validate_spec(repo, frozen_spec)
    execution_platform = cast(str, spec["platform"])
    isolation_revision = _isolation_revision_sha256(execution_platform)
    bindings_relative = Path("swebench") / cast(str, spec["scope"]) / "image-bindings.json"
    attestation_relative = Path("swebench-image-attestations") / f"{spec_sha}.json"
    scope_relative = Path("swebench") / cast(str, spec["scope"])
    host_spec_relatives = {
        instance_id: scope_relative / "host-test-specs" / f"{instance_id}.json"
        for instance_id in cast(list[str], spec["instance_ids"])
        if cast(str, spec["scope"]) in _OFFICIAL_SCOPES
    }
    for relative in (bindings_relative, attestation_relative):
        destination = _private_destination(state, relative)
        if destination.exists():
            raise BuildError(
                "private artifact already exists; refusing to overwrite frozen evidence"
            )
    docker = str(docker_executable)
    try:
        engine = _mapping(
            json.loads(_run(runner, (docker, "info", "--format", "{{json .}}")).stdout),
            "Docker engine",
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError("Docker engine evidence is invalid") from exc
    if engine.get("OSType") != "linux" or engine.get("Architecture") not in {"aarch64", "arm64"}:
        raise BuildError("Docker must provide a linux/arm64 runtime")
    control_reference = cast(str, spec["control_image_reference"])
    control = _json_first(
        _run(
            runner,
            (docker, "image", "inspect", f"--platform={CONTROL_PLATFORM}", control_reference),
        ),
        "control image",
    )
    control_repo_digests = control.get("RepoDigests")
    if (
        not isinstance(control_repo_digests, list)
        or any(not isinstance(reference, str) for reference in control_repo_digests)
        or control_reference not in control_repo_digests
        or control.get("Os") != "linux"
        or control.get("Architecture") not in {"aarch64", "arm64"}
        or control.get("Variant") not in {None, "v8"}
    ):
        raise BuildError("control image digest or linux/arm64/v8 platform evidence is invalid")

    limits = _mapping(spec["resource_limits"], "resource_limits")
    images_evidence: list[dict[str, object]] = []
    host_test_spec_sources: dict[str, bytes] = {}
    host_test_spec_documents: dict[str, Mapping[str, Any]] = {}
    for raw_image in cast(list[object], spec["images"]):
        image = _mapping(raw_image, "image")
        role = cast(str, image["role"])
        instance_id = cast(str, image["instance_id"])
        tag = cast(str, image["tag"])
        if role == "grader" and image["host_test_spec_path"] is not None:
            spec_source = _outside_repo(
                Path(cast(str, image["host_test_spec_path"])), repo, "host TestSpec"
            )
            spec_bytes = _private_file(spec_source, "host TestSpec")
            if _sha256(spec_bytes) != image["host_test_spec_sha256"]:
                raise BuildError("host TestSpec fingerprint drifted before build")
            host_test_spec_sources[instance_id] = spec_bytes
            try:
                host_test_spec_documents[instance_id] = _mapping(
                    json.loads(spec_bytes), "host TestSpec"
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BuildError("host TestSpec is invalid before build") from exc
        context = Path(cast(str, image["context"])).resolve(strict=True)
        dockerfile = context / cast(str, image["dockerfile"])
        context_sha256 = cast(str, image["context_sha256"])
        build_args = (
            docker,
            "buildx",
            "build",
            "--network=none",
            f"--platform={execution_platform}",
            "--pull=false",
            "--no-cache",
            "--provenance=false",
            "--sbom=false",
            "--load",
            "--label",
            f"{BUILD_INPUTS_LABEL}={context_sha256}",
            "--tag",
            tag,
            "--file",
            str(dockerfile),
            str(context),
        )
        reused = reuse_existing_images and _existing_image_matches(
            docker, runner, tag, execution_platform, context_sha256
        )
        if not reused:
            _run(runner, build_args)
        inspected = _json_first(
            _run(
                runner,
                (docker, "image", "inspect", f"--platform={execution_platform}", tag),
            ),
            "image",
        )
        image_id = inspected.get("Id")
        expected_architectures = (
            {"amd64", "x86_64"}
            if execution_platform == QUALIFICATION_PLATFORM
            else {"aarch64", "arm64"}
        )
        expected_variants = (
            {None, ""} if execution_platform == QUALIFICATION_PLATFORM else {None, "v8"}
        )
        if (
            not isinstance(image_id, str)
            or _IMAGE_ID_RE.fullmatch(image_id) is None
            or inspected.get("Os") != "linux"
            or inspected.get("Architecture") not in expected_architectures
            or inspected.get("Variant") not in expected_variants
        ):
            raise BuildError(f"image identity or {execution_platform} platform evidence is invalid")
        binding_sha = _sha256(f"{instance_id}\0{role}".encode())[:12]
        immutable_reference = _immutable_reference(tag, image_id)
        immutable = _json_first(
            _run(
                runner,
                (
                    docker,
                    "image",
                    "inspect",
                    f"--platform={execution_platform}",
                    immutable_reference,
                ),
            ),
            "immutable image",
        )
        if immutable.get("Id") != image_id:
            raise BuildError("immutable image reference does not resolve to the built image")
        name = f"racecraft-swebench-attest-{spec_sha[:12]}-{binding_sha}"
        runtime_contract = _attest_runtime(
            docker, runner, tag, image_id, name, limits, execution_platform
        )
        host_test_spec_document = (
            host_test_spec_documents.get(instance_id) if role == "grader" else None
        )
        images_evidence.append(
            {
                "role": role,
                "instance_id": instance_id,
                "base_commit": image["base_commit"],
                "source_record_sha256": image["source_record_sha256"],
                "context_sha256": image["context_sha256"],
                "reused_existing_image": reused,
                "build_recipe_sha256": _sha256(dockerfile.read_bytes()),
                "official_scorer_revision_sha256": image["official_scorer_revision_sha256"],
                "trusted_tests_sha256": image["trusted_tests_sha256"],
                "host_test_spec_path": (
                    host_spec_relatives[instance_id].as_posix()
                    if instance_id in host_spec_relatives and role == "grader"
                    else None
                ),
                "host_test_spec_sha256": image["host_test_spec_sha256"],
                "source_eval_script_sha256": (
                    host_test_spec_document.get("source_eval_script_sha256")
                    if host_test_spec_document is not None
                    else None
                ),
                "test_spec_sha256": (
                    host_test_spec_document.get("test_spec_sha256")
                    if host_test_spec_document is not None
                    else None
                ),
                "canonical_eval_script_sha256": (
                    host_test_spec_document.get("canonical_eval_script_sha256")
                    if host_test_spec_document is not None
                    else None
                ),
                "execution_eval_script_sha256": (
                    host_test_spec_document.get("execution_eval_script_sha256")
                    if host_test_spec_document is not None
                    else None
                ),
                "scorer_entrypoint_sha256": (
                    host_test_spec_document.get("scorer_entrypoint_sha256")
                    if host_test_spec_document is not None
                    else None
                ),
                "image_id": image_id,
                "image_digest": image_id,
                "image_reference": immutable_reference,
                "platform": execution_platform,
                "runtime_contract": runtime_contract,
            }
        )

    ordered_ids = cast(list[str], spec["instance_ids"])
    ordered_ids_sha256 = _sha256(_canonical(ordered_ids))
    image_by_binding = {
        (cast(str, image["instance_id"]), cast(str, image["role"])): image
        for image in images_evidence
    }
    host_test_spec_hashes: dict[str, str] = {}
    for instance_id, relative in host_spec_relatives.items():
        payload = host_test_spec_sources.get(instance_id)
        if payload is None:
            raise BuildError("qualification host TestSpec source is unavailable")
        destination = _private_destination(state, relative)
        if destination.exists():
            existing = _private_file(destination, "existing private host TestSpec")
            if existing != payload:
                raise BuildError("existing private host TestSpec differs from frozen source")
            host_test_spec_hashes[instance_id] = _sha256(existing)
        else:
            host_test_spec_hashes[instance_id] = _write_private_bytes(state, relative, payload)
        grader_sha = image_by_binding[(instance_id, "grader")]["host_test_spec_sha256"]
        if host_test_spec_hashes[instance_id] != grader_sha:
            raise BuildError("persisted host TestSpec fingerprint changed")
    bindings: list[dict[str, object]] = []
    for instance_id in ordered_ids:
        task = image_by_binding[(instance_id, "task")]
        grader = image_by_binding[(instance_id, "grader")]
        bindings.append(
            {
                "instance_id": instance_id,
                "base_commit": task["base_commit"],
                "source_record_sha256": task["source_record_sha256"],
                "task_image_reference": task["image_reference"],
                "task_image_digest": task["image_digest"],
                "task_build_recipe_sha256": task["build_recipe_sha256"],
                "task_build_inputs_sha256": task["context_sha256"],
                "grader_image_reference": grader["image_reference"],
                "grader_image_digest": grader["image_digest"],
                "grader_build_recipe_sha256": grader["build_recipe_sha256"],
                "grader_build_inputs_sha256": grader["context_sha256"],
                "official_scorer_revision_sha256": grader["official_scorer_revision_sha256"],
                "trusted_tests_sha256": grader["trusted_tests_sha256"],
                "host_test_spec_path": grader["host_test_spec_path"],
                "host_test_spec_sha256": grader["host_test_spec_sha256"],
                "execution_eval_script_sha256": grader["execution_eval_script_sha256"],
            }
        )
    binding_document: dict[str, object] = {
        "schema_version": 1,
        "benchmark": "swebench_verified",
        "mode": spec["scope"],
        "execution_platform": execution_platform,
        "task_count": len(ordered_ids),
        "ordered_instance_ids_sha256": ordered_ids_sha256,
        "control_image_reference": spec["control_image_reference"],
        "control_image_digest": spec["control_image_digest"],
        "isolation_revision_sha256": isolation_revision,
        "bindings": bindings,
    }
    bindings_sha = _write_private_json(state, bindings_relative, binding_document)

    evidence: dict[str, object] = {
        "schema_version": 1,
        "scope": spec["scope"],
        "spec_sha256": spec_sha,
        "platform": execution_platform,
        "ordered_instance_ids_sha256": ordered_ids_sha256,
        "task_count": len(ordered_ids),
        "image_bindings_sha256": bindings_sha,
        "isolation_revision_sha256": isolation_revision,
        "images": images_evidence,
    }
    attestation_sha = _write_private_json(
        state,
        attestation_relative,
        evidence,
    )
    return {
        "schema_version": 1,
        "scope": spec["scope"],
        "task_count": len(ordered_ids),
        "image_count": len(images_evidence),
        "image_bindings_sha256": bindings_sha,
        "ordered_instance_ids_sha256": ordered_ids_sha256,
        "isolation_revision_sha256": isolation_revision,
        "attestation_sha256": attestation_sha,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--docker", type=Path, default=Path("/usr/local/bin/docker"))
    parser.add_argument(
        "--reuse-existing-images",
        action="store_true",
        help="skip rebuilding a tag whose label binds the same frozen build inputs",
    )
    args = parser.parse_args(argv)
    summary = build_images(
        args.repo_root,
        args.state_root,
        args.spec,
        args.docker,
        reuse_existing_images=args.reuse_existing_images,
    )
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
