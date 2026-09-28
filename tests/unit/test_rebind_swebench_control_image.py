from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import rebind_swebench_control_image as rebind  # noqa: E402


def test_rebound_binding_changes_only_control_image() -> None:
    new_digest = "sha256:" + "e" * 64
    new_reference = f"{rebind.CONTROL_IMAGE_REPOSITORY}@{new_digest}"
    old = {
        "schema_version": 1,
        "benchmark": "swebench_verified",
        "mode": "qualification",
        "execution_platform": "linux/amd64",
        "task_count": 1,
        "ordered_instance_ids_sha256": "a" * 64,
        "control_image_reference": "local/swebench-control@sha256:" + "b" * 64,
        "control_image_digest": "sha256:" + "b" * 64,
        "isolation_revision_sha256": "c" * 64,
        "bindings": [{"instance_id": "example__repo-1", "task_image_digest": "d" * 64}],
    }
    raw = json.dumps(old, sort_keys=True).encode()
    changed = rebind._rebound_document(
        raw,
        hashlib.sha256(raw).hexdigest(),
        {"reference": old["control_image_reference"], "digest": old["control_image_digest"]},
        new_reference,
        new_digest,
    )
    assert changed["control_image_reference"] == new_reference
    assert changed["control_image_digest"] == new_digest
    assert {
        key: value
        for key, value in changed.items()
        if key not in {"control_image_reference", "control_image_digest"}
    } == {
        key: value
        for key, value in old.items()
        if key not in {"control_image_reference", "control_image_digest"}
    }


def test_rebound_binding_rejects_tampered_source_bytes() -> None:
    raw = b'{"control_image_digest":"sha256:' + b"b" * 64 + b'"}'
    new_digest = "sha256:" + "e" * 64
    with pytest.raises(rebind.RebindError, match="frozen SHA-256"):
        rebind._rebound_document(
            raw,
            "0" * 64,
            {
                "reference": "local/swebench-control@sha256:" + "b" * 64,
                "digest": "sha256:" + "b" * 64,
            },
            f"{rebind.CONTROL_IMAGE_REPOSITORY}@{new_digest}",
            new_digest,
        )


def test_target_image_requires_matching_immutable_reference() -> None:
    digest = "sha256:" + "e" * 64
    reference, verified_digest = rebind._control_image_identity(digest)
    assert reference == f"{rebind.CONTROL_IMAGE_REPOSITORY}@{digest}"
    assert verified_digest == digest

    with pytest.raises(rebind.RebindError, match="must match the deterministic digest reference"):
        rebind._control_image_identity(digest, "local/swebench-control:latest")


def test_versioned_outputs_include_source_and_target_identity() -> None:
    source_sha = "a" * 64
    target_digest = "sha256:" + "e" * 64
    assert rebind._versioned_binding_path(source_sha, target_digest) == (
        "swebench/qualification/image-bindings-control-eeeeeeeeeeee-from-aaaaaaaaaaaa.json"
    )
    assert rebind._versioned_provenance_path(source_sha, target_digest) == (
        "swebench/qualification/control-image-rebind-aaaaaaaaaaaa-eeeeeeeeeeee.json"
    )


def test_cli_requires_source_binding_and_target_digest(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        rebind.main(["--project-root", str(tmp_path), "--state-dir", str(tmp_path)])
    assert error.value.code == 2


def test_profile_update_changes_only_binding_path_and_hash() -> None:
    raw = (
        "schema_version: 1\n"
        "suite: swebench-qualification\n"
        "swebench:\n"
        "  mode: qualification\n"
        "  image_bindings_path: swebench/qualification/image-bindings.json\n"
        f"  image_bindings_sha256: {'a' * 64}\n"
        "  control_image_digest: sha256:abc\n"
    ).encode()
    changed = rebind._updated_profile_bytes(
        raw,
        "swebench/qualification/image-bindings.json",
        "a" * 64,
        "swebench/qualification/image-bindings-control-new.json",
        "f" * 64,
    )
    assert b"  control_image_digest: sha256:abc\n" in changed
    assert changed.count(b"image_bindings_path:") == 1
    assert changed.count(b"image_bindings_sha256:") == 1


def test_private_versioned_file_refuses_overwrite(tmp_path: Path) -> None:
    destination = tmp_path / "versioned.json"
    rebind._write_private_once(destination, b"first")
    with pytest.raises(rebind.RebindError, match="refusing to overwrite"):
        rebind._write_private_once(destination, b"second")
    assert destination.read_bytes() == b"first"
