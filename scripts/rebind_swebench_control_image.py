#!/usr/bin/env python3
"""Version a qualification binding for a newly verified control image."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import yaml

from local_evals import swebench
from local_evals.config import ExternalStatePaths

PROFILE_PATH = Path("configs/profiles/swebench-qualification.yaml")
CONTROL_IMAGE_REPOSITORY = "local/swebench-control"
_INSPECT_FORMAT = "{{.Id}} {{.Os}}/{{.Architecture}} {{json .RepoDigests}}"


class RebindError(ValueError):
    """A binding could not be safely carried to the verified control image."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _control_image_identity(digest: object, reference: object | None = None) -> tuple[str, str]:
    if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
        raise RebindError("new control image digest must be an immutable SHA-256 digest")
    if any(char not in "0123456789abcdef" for char in digest.removeprefix("sha256:")):
        raise RebindError("new control image digest must be an immutable SHA-256 digest")
    expected_reference = f"{CONTROL_IMAGE_REPOSITORY}@{digest}"
    if reference is None:
        return expected_reference, digest
    if not isinstance(reference, str) or reference != expected_reference:
        raise RebindError(
            "new control image reference must match the deterministic digest reference"
        )
    return reference, digest


def _versioned_binding_path(source_sha256: str, target_digest: str) -> str:
    target_hex = target_digest.removeprefix("sha256:")
    return (
        "swebench/qualification/image-bindings-control-"
        f"{target_hex[:12]}-from-{source_sha256[:12]}.json"
    )


def _versioned_provenance_path(source_sha256: str, target_digest: str) -> str:
    target_hex = target_digest.removeprefix("sha256:")
    return (
        f"swebench/qualification/control-image-rebind-{source_sha256[:12]}-{target_hex[:12]}.json"
    )


def _load_yaml(raw: bytes) -> dict[str, Any]:
    document = yaml.safe_load(raw)
    if not isinstance(document, dict) or not isinstance(document.get("swebench"), dict):
        raise RebindError("qualification profile shape is invalid")
    return document


def _inspect_image(docker: str, reference: str) -> tuple[str, str, list[str]]:
    result = subprocess.run(  # noqa: S603 - docker is locally resolved; image refs are frozen digests
        [docker, "image", "inspect", reference, "--format", _INSPECT_FORMAT],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise RebindError(f"Docker cannot inspect immutable image {reference}")
    parts = result.stdout.strip().split(" ", 2)
    if len(parts) != 3:
        raise RebindError(f"Docker returned incomplete image identity for {reference}")
    try:
        repo_digests = json.loads(parts[2])
    except json.JSONDecodeError as exc:
        raise RebindError(f"Docker returned invalid repository digests for {reference}") from exc
    if not isinstance(repo_digests, list) or any(
        not isinstance(item, str) for item in repo_digests
    ):
        raise RebindError(f"Docker returned invalid repository digests for {reference}")
    return parts[0], parts[1], repo_digests


def _verify_image(
    reference: object,
    digest: object,
    expected_platform: str,
    inspect: Callable[[str], tuple[str, str, list[str]]],
) -> None:
    if (
        not isinstance(reference, str)
        or not isinstance(digest, str)
        or "@" not in reference
        or reference.rsplit("@", 1)[1] != digest
        or not digest.startswith("sha256:")
        or len(digest) != 71
    ):
        raise RebindError("an image binding is not an immutable SHA-256 reference")
    image_id, platform, repo_digests = inspect(reference)
    if image_id != digest or platform != expected_platform or reference not in repo_digests:
        raise RebindError(f"immutable image identity or platform changed: {reference}")


def _rebound_document(
    raw: bytes,
    expected_sha256: str,
    old: Mapping[str, Any],
    new_reference: str,
    new_digest: str,
) -> dict[str, Any]:
    if _sha256(raw) != expected_sha256:
        raise RebindError("source binding bytes do not match the frozen SHA-256")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RebindError("source binding JSON is invalid") from exc
    if not isinstance(document, dict):
        raise RebindError("source binding JSON is not an object")
    updated = dict(document)
    updated["control_image_reference"] = new_reference
    updated["control_image_digest"] = new_digest
    before = {
        key: value
        for key, value in document.items()
        if key not in {"control_image_reference", "control_image_digest"}
    }
    after = {
        key: value
        for key, value in updated.items()
        if key not in {"control_image_reference", "control_image_digest"}
    }
    if (
        before != after
        or document.get("control_image_reference") != old.get("reference")
        or document.get("control_image_digest") != old.get("digest")
    ):
        raise RebindError("source binding identity changed before rebind")
    return updated


def _updated_profile_bytes(
    raw: bytes, old_path: str, old_sha256: str, new_path: str, new_sha256: str
) -> bytes:
    text = raw.decode("utf-8")
    pairs = (
        (f"  image_bindings_path: {old_path}\n", f"  image_bindings_path: {new_path}\n"),
        (f"  image_bindings_sha256: {old_sha256}\n", f"  image_bindings_sha256: {new_sha256}\n"),
    )
    for before, after in pairs:
        if text.count(before) != 1:
            raise RebindError("qualification profile binding fields changed unexpectedly")
        text = text.replace(before, after, 1)
    updated = text.encode("utf-8")
    old_document = _load_yaml(raw)
    new_document = _load_yaml(updated)
    old_profile = old_document["swebench"]
    new_profile = new_document["swebench"]
    if any(
        old_profile[key] != new_profile[key]
        for key in old_profile.keys() - {"image_bindings_path", "image_bindings_sha256"}
    ):
        raise RebindError("profile update changed fields outside the image binding path and hash")
    if (
        new_profile["image_bindings_path"] != new_path
        or new_profile["image_bindings_sha256"] != new_sha256
    ):
        raise RebindError("qualification profile update did not bind the new file")
    return updated


def _write_private_once(path: Path, raw: bytes) -> None:
    parent_stat = path.parent.stat()
    if stat.S_IMODE(parent_stat.st_mode) & 0o077:
        raise RebindError("private binding directory must be owner-only")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise RebindError(f"refusing to overwrite versioned private file: {path}") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RebindError("private binding file permissions are not 0600")


def _replace_profile(path: Path, old_raw: bytes, new_raw: bytes) -> None:
    if path.read_bytes() != old_raw:
        raise RebindError("qualification profile changed during rebind; refusing update")
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, temporary_name = tempfile.mkstemp(prefix=".swebench-profile-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(new_raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        if path.read_bytes() != old_raw:
            raise RebindError("qualification profile changed during rebind; refusing update")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def rebind(
    *,
    project_root: Path,
    state_dir: Path,
    source_binding_sha256: str,
    new_control_image_digest: str,
    new_control_image_reference: str | None = None,
    inspect: Callable[[str], tuple[str, str, list[str]]] | None = None,
) -> dict[str, str]:
    if (
        not isinstance(source_binding_sha256, str)
        or len(source_binding_sha256) != 64
        or any(char not in "0123456789abcdef" for char in source_binding_sha256)
    ):
        raise RebindError("source binding SHA-256 must be 64 lowercase hexadecimal characters")
    target_reference, target_digest = _control_image_identity(
        new_control_image_digest, new_control_image_reference
    )
    paths = ExternalStatePaths.resolve(
        project_root=project_root, state_dir=state_dir, require_project=True, require_state=True
    )
    profile_path = paths.project_root / PROFILE_PATH
    profile_raw = profile_path.read_bytes()
    profile_document = _load_yaml(profile_raw)
    profile = profile_document["swebench"]
    if profile.get("mode") != "qualification":
        raise RebindError("this utility only updates the frozen qualification profile")
    if (
        profile.get("control_image_reference") != target_reference
        or profile.get("control_image_digest") != target_digest
    ):
        raise RebindError(
            "qualification profile must already be bound to the requested ARM64 control image"
        )
    old_sha = profile.get("image_bindings_sha256")
    old_path_value = profile.get("image_bindings_path")
    if old_sha != source_binding_sha256 or not isinstance(old_path_value, str):
        raise RebindError("qualification profile no longer matches the expected source binding")
    old_relative = Path(old_path_value)
    if old_relative.is_absolute() or old_relative.parts[:2] != ("swebench", "qualification"):
        raise RebindError("source image binding path must remain in private qualification state")
    old_path = paths.state_dir / old_relative
    swebench._private_path(old_path, paths.state_dir, "source image bindings")
    old_raw = old_path.read_bytes()
    try:
        old_document = json.loads(old_raw)
    except json.JSONDecodeError as exc:
        raise RebindError("source image binding JSON is invalid") from exc
    if not isinstance(old_document, Mapping):
        raise RebindError("source image binding JSON is not an object")
    old_control = {
        "reference": old_document.get("control_image_reference"),
        "digest": old_document.get("control_image_digest"),
    }
    if old_control["reference"] == target_reference and old_control["digest"] == target_digest:
        raise RebindError("source image binding already references the requested control image")
    source_profile = dict(profile)
    source_profile["control_image_reference"] = old_control["reference"]
    source_profile["control_image_digest"] = old_control["digest"]
    tasks, manifest_sha256, _, _ = swebench._validate_manifest(source_profile, paths.state_dir)
    old_entries = swebench._validated_binding_entries(old_document, source_profile, tasks)
    for task, entry in zip(tasks, old_entries, strict=True):
        swebench._validated_bound_task(task, entry, state=paths.state_dir, mode="qualification")
    docker = shutil.which("docker") or "docker"
    image_inspector = inspect or (lambda reference: _inspect_image(docker, reference))
    _verify_image(old_control["reference"], old_control["digest"], "linux/arm64", image_inspector)
    for entry in old_entries:
        binding = cast(Mapping[str, Any], entry)
        _verify_image(
            binding["task_image_reference"],
            binding["task_image_digest"],
            "linux/amd64",
            image_inspector,
        )
        _verify_image(
            binding["grader_image_reference"],
            binding["grader_image_digest"],
            "linux/amd64",
            image_inspector,
        )
    _verify_image(target_reference, target_digest, "linux/arm64", image_inspector)

    new_document = _rebound_document(
        old_raw, source_binding_sha256, old_control, target_reference, target_digest
    )
    new_profile = dict(profile)
    new_profile["image_bindings_path"] = _versioned_binding_path(
        source_binding_sha256, target_digest
    )
    new_profile["image_bindings_sha256"] = _sha256(swebench._canonical(new_document))
    new_entries = swebench._validated_binding_entries(new_document, new_profile, tasks)
    for task, entry in zip(tasks, new_entries, strict=True):
        swebench._validated_bound_task(task, entry, state=paths.state_dir, mode="qualification")
    new_binding_raw = swebench._canonical(new_document)
    new_binding_sha = _sha256(new_binding_raw)
    binding_path = paths.state_dir / new_profile["image_bindings_path"]
    provenance_relative = _versioned_provenance_path(source_binding_sha256, target_digest)
    provenance_path = paths.state_dir / provenance_relative
    if binding_path.exists() or provenance_path.exists():
        raise RebindError("versioned rebind output already exists; refusing overwrite")
    new_profile["image_bindings_sha256"] = new_binding_sha
    profile_new_raw = _updated_profile_bytes(
        profile_raw,
        old_path_value,
        source_binding_sha256,
        cast(str, new_profile["image_bindings_path"]),
        new_binding_sha,
    )
    profile_document_new = _load_yaml(profile_new_raw)
    final_profile = profile_document_new["swebench"]
    final_profile.update(
        {
            "image_bindings_path": new_profile["image_bindings_path"],
            "image_bindings_sha256": new_binding_sha,
        }
    )
    receipt_path: str | None = None
    receipt_sha: str | None = None
    expected_runtime_identity = profile.get("model_runtime", {}).get("runtime_identity_sha256")
    runtime_directory = paths.state_dir / "swebench/qualification/runtime-identity"
    for candidate in sorted(runtime_directory.glob("runtime-identity-*.json")):
        receipt_raw = candidate.read_bytes()
        try:
            receipt = json.loads(receipt_raw)
        except json.JSONDecodeError:
            continue
        model_runtime = receipt.get("model_runtime") if isinstance(receipt, Mapping) else None
        if (
            isinstance(model_runtime, Mapping)
            and model_runtime.get("runtime_identity_sha256") == expected_runtime_identity
        ):
            receipt_path = str(candidate.relative_to(paths.state_dir))
            receipt_sha = _sha256(receipt_raw)
            break
    if receipt_path is None or receipt_sha is None:
        raise RebindError(
            "could not link the qualification profile to its historical runtime receipt"
        )

    # Write the candidate privately, then run the full existing validator against its frozen bytes.
    _write_private_once(binding_path, new_binding_raw)
    final_profile["image_bindings_path"] = str(new_profile["image_bindings_path"])
    final_profile["image_bindings_sha256"] = new_binding_sha
    prepared = swebench._validate_profile(final_profile, paths.project_root, paths.state_dir)
    unchanged_images = [
        {
            "instance_id": cast(Mapping[str, Any], entry)["instance_id"],
            "task_image_digest": cast(Mapping[str, Any], entry)["task_image_digest"],
            "grader_image_digest": cast(Mapping[str, Any], entry)["grader_image_digest"],
        }
        for entry in new_entries
    ]
    provenance = {
        "schema_version": 1,
        "evidence_class": "swebench_control_image_binding_rebind",
        "record_type": "chained_local_binding_record",
        "builder_attestation": False,
        "changed_fields": ["control_image_reference", "control_image_digest"],
        "old_binding_path": str(old_relative),
        "old_binding_sha256": source_binding_sha256,
        "new_binding_path": str(new_profile["image_bindings_path"]),
        "new_binding_sha256": new_binding_sha,
        "old_control_image": old_control,
        "new_control_image": {
            "reference": target_reference,
            "digest": target_digest,
            "platform": "linux/arm64",
        },
        "unchanged_task_grader_images": unchanged_images,
        "ordered_instance_ids_sha256": new_document["ordered_instance_ids_sha256"],
        "manifest_sha256": manifest_sha256,
        "historical_runtime_receipt": {
            "path": receipt_path,
            "file_sha256": receipt_sha,
            "runtime_identity_sha256": expected_runtime_identity,
        },
        "original_profile_sha256": _sha256(profile_raw),
        "final_profile_sha256": _sha256(profile_new_raw),
        "final_protocol_fingerprint_sha256": prepared.protocol_fingerprint,
        "statement": (
            "This record links frozen bindings across a control-image change; "
            "it is not a builder-produced attestation."
        ),
    }
    provenance_raw = swebench._canonical(provenance)
    _write_private_once(provenance_path, provenance_raw)
    _replace_profile(profile_path, profile_raw, profile_new_raw)
    return {
        "binding_path": str(binding_path),
        "binding_sha256": new_binding_sha,
        "provenance_path": str(provenance_path),
        "profile_sha256": _sha256(profile_new_raw),
        "protocol_fingerprint_sha256": prepared.protocol_fingerprint,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--state-dir", type=Path, default=Path("~/.local/state/splash-evals").expanduser()
    )
    parser.add_argument(
        "--source-binding-sha256",
        required=True,
        help="SHA-256 of the exact private source image binding JSON",
    )
    parser.add_argument(
        "--new-control-image-digest",
        required=True,
        help="SHA-256 digest of the newly attested ARM64 control image",
    )
    parser.add_argument(
        "--new-control-image-reference",
        help=(
            "optional immutable reference; must equal "
            "local/swebench-control@<new-control-image-digest>"
        ),
    )
    args = parser.parse_args(argv)
    result = rebind(
        project_root=args.project_root,
        state_dir=args.state_dir,
        source_binding_sha256=args.source_binding_sha256,
        new_control_image_digest=args.new_control_image_digest,
        new_control_image_reference=args.new_control_image_reference,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
