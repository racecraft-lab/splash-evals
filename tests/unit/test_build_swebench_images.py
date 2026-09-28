from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

SCRIPT = Path(__file__).parents[2] / "scripts" / "build_swebench_images.py"
_CONTROL_DIGEST = f"sha256:{'c' * 64}"
_CANONICAL_EVAL_SCRIPT = (
    b"#!/bin/bash\n"
    b"set -uxo pipefail\n"
    b"pytest -q tests/test_frozen.py\n"
    b"SWEBENCH_TEST_EXIT_CODE=$?\n"
    b": '>>>>> End Test Output'\n"
    b'echo ">>>>> Test Exit Code: $SWEBENCH_TEST_EXIT_CODE"\n'
    b"git checkout -- tests/test_frozen.py\n"
)
_EXECUTION_EVAL_SCRIPT = _CANONICAL_EVAL_SCRIPT + b'exit "$SWEBENCH_TEST_EXIT_CODE"\n'


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_swebench_images", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_swebench_contract() -> ModuleType:
    path = Path(__file__).with_name("test_swebench.py")
    spec = importlib.util.spec_from_file_location("builder_test_swebench_contract", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_script()


def test_execution_eval_script_accepts_official_five_greater_than_markers() -> None:
    assert builder._execution_eval_script(_CANONICAL_EVAL_SCRIPT) == _EXECUTION_EVAL_SCRIPT


@pytest.mark.parametrize(
    "canonical_eval_script",
    [
        _CANONICAL_EVAL_SCRIPT.replace(b": '>>>>> End Test Output'\n", b""),
        _CANONICAL_EVAL_SCRIPT.replace(
            b'echo ">>>>> Test Exit Code: $SWEBENCH_TEST_EXIT_CODE"\n',
            b'echo ">>>>>> Test Exit Code: $SWEBENCH_TEST_EXIT_CODE"\n',
        ),
        _CANONICAL_EVAL_SCRIPT.replace(
            b": '>>>>> End Test Output'\n",
            b": '>>>>> End Test Output'\n: '>>>>> End Test Output'\n",
        ),
    ],
    ids=["missing-end-marker", "malformed-exit-marker", "duplicate-end-marker"],
)
def test_refuses_generated_script_with_missing_malformed_or_ambiguous_marker(
    canonical_eval_script: bytes,
) -> None:
    with pytest.raises(builder.BuildError, match="unambiguous test exit marker"):
        builder._execution_eval_script(canonical_eval_script)


def test_refuses_generated_script_with_markers_out_of_order() -> None:
    canonical_eval_script = _CANONICAL_EVAL_SCRIPT.replace(
        b": '>>>>> End Test Output'\necho \">>>>> Test Exit Code: $SWEBENCH_TEST_EXIT_CODE\"\n",
        b"echo \">>>>> Test Exit Code: $SWEBENCH_TEST_EXIT_CODE\"\n: '>>>>> End Test Output'\n",
    )
    with pytest.raises(builder.BuildError, match="marker ordering is unexpected"):
        builder._execution_eval_script(canonical_eval_script)


@pytest.mark.parametrize(
    ("role_index", "replacement", "message"),
    [
        (
            1,
            f"FROM --platform=linux/amd64 ubuntu@sha256:{'b' * 64}",
            "does not match frozen task.yaml image",
        ),
        (0, "FROM scratch", "must use one immutable linux/amd64 base"),
    ],
    ids=["generic-ubuntu", "missing-base-provenance"],
)
def test_refuses_unproven_qualification_base_image(
    tmp_path: Path, role_index: int, replacement: str, message: str
) -> None:
    spec = _spec(tmp_path)
    payload = json.loads(spec.read_bytes())
    image = payload["images"][role_index]
    context = Path(image["context"])
    dockerfile = context / "Dockerfile"
    lines = dockerfile.read_text(encoding="utf-8").splitlines()
    lines[0] = replacement
    dockerfile.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dockerfile.chmod(0o600)
    image["context_sha256"] = _tree_hash(context)
    spec.write_text(json.dumps(payload), encoding="utf-8")
    spec.chmod(0o600)
    docker = FakeDocker()

    with pytest.raises(builder.BuildError, match=message):
        builder.build_images(tmp_path / "repo", tmp_path / "state", spec, Path("docker"), docker)

    assert docker.commands == []


def test_refuses_different_task_and_grader_base_image_digests(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    payload = json.loads(spec.read_bytes())
    task_image = payload["images"][0]
    task_context = Path(task_image["context"])
    dockerfile = task_context / "Dockerfile"
    lines = dockerfile.read_text(encoding="utf-8").splitlines()
    reference = lines[0].rsplit(" ", 1)[1]
    repository = reference.rsplit("@", 1)[0]
    lines[0] = f"FROM --platform=linux/amd64 {repository}@sha256:{'c' * 64}"
    dockerfile.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dockerfile.chmod(0o600)
    task_image["context_sha256"] = _tree_hash(task_context)
    spec.write_text(json.dumps(payload), encoding="utf-8")
    spec.chmod(0o600)
    docker = FakeDocker()

    with pytest.raises(builder.BuildError, match="same immutable per-instance base image"):
        builder.build_images(tmp_path / "repo", tmp_path / "state", spec, Path("docker"), docker)

    assert docker.commands == []


@pytest.mark.parametrize(
    "container_env",
    [
        ["HOME=/tmp/home", "LANG=C.UTF-8"],
        ["HOME=/tmp/home", "HOME=/tmp/home", "PATH=/usr/local/bin:/usr/bin:/bin"],
        ["HOME=/tmp/home", "PATH=/usr/local/bin:/usr/bin:/bin", "MALFORMED"],
    ],
    ids=["missing-path-override", "duplicate-name", "malformed-entry"],
)
def test_runtime_attestation_rejects_missing_or_malformed_environment(
    tmp_path: Path, container_env: Sequence[str]
) -> None:
    spec = _spec(tmp_path)
    docker = FakeDocker(container_env=container_env)

    with pytest.raises(builder.BuildError, match="runtime safety contract"):
        builder.build_images(tmp_path / "repo", tmp_path / "state", spec, Path("docker"), docker)


def test_control_docker_context_includes_frozen_scorer_entrypoint() -> None:
    context = SCRIPT.parents[1] / "sandbox" / "swebench"
    dockerignore = (context / ".dockerignore").read_text(encoding="utf-8").splitlines()
    dockerfile = (context / "Dockerfile").read_text(encoding="utf-8")
    scorer_source = context / "scorer_entrypoint.py"

    assert "*" in dockerignore
    assert "!scorer_entrypoint.py" in dockerignore
    assert (
        "COPY --chown=65532:65532 scorer_entrypoint.py /opt/racecraft/scorer_entrypoint.py"
    ) in dockerfile
    assert hashlib.sha256(scorer_source.read_bytes()).hexdigest() == (
        builder.SCORER_ENTRYPOINT_SHA256
    )


def test_control_overlay_is_pinned_to_frozen_parent_and_scorer() -> None:
    repo = SCRIPT.parents[1]
    overlay = repo / "sandbox" / "swebench" / "control-overlay"
    dockerfile = (overlay / "Dockerfile").read_text(encoding="utf-8")
    scorer_source = repo / "sandbox" / "swebench" / "scorer_entrypoint.py"

    assert (
        "FROM local/swebench-control@sha256:"
        "21a8cc29bc180fa4b5460c0f52d1bd8030b44ddf2c00158d0de18fa15b8f7297"
    ) in dockerfile
    assert (
        "COPY --chown=0:0 --chmod=0444 scorer_entrypoint.py /opt/racecraft/scorer_entrypoint.py"
    ) in dockerfile
    assert builder.SCORER_ENTRYPOINT_SHA256 in dockerfile
    assert hashlib.sha256(scorer_source.read_bytes()).hexdigest() == (
        builder.SCORER_ENTRYPOINT_SHA256
    )
    assert not (overlay / "wheels").exists()


def _tree_hash(context: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(context.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(context).as_posix()
        if path.is_file():
            digest.update(f"file\0{relative}\0{stat.S_IMODE(path.stat().st_mode):04o}\0".encode())
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _context(root: Path, name: str, base_reference: str | None = None) -> Path:
    context = root / name
    context.mkdir(mode=0o700)
    dockerfile = context / "Dockerfile"
    contents = (
        "FROM scratch\nUSER 65532:65532\n"
        if base_reference is None
        else f"FROM --platform=linux/amd64 {base_reference}\nUSER 65532:65532\n"
    )
    dockerfile.write_text(contents, encoding="utf-8")
    dockerfile.chmod(0o600)
    return context


def _spec(
    tmp_path: Path,
    *,
    scope: str = "qualification",
    task_count: int = 2,
    source_tasks: Sequence[Mapping[str, Any]] | None = None,
    selection_manifest_path: Path | None = None,
    selection_manifest_sha256: str | None = None,
    platform: str | None = None,
    control_reference: str = f"local/swebench-control@{_CONTROL_DIGEST}",
    control_digest: str = _CONTROL_DIGEST,
) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    scorer_source = repo_root / "sandbox" / "swebench" / "scorer_entrypoint.py"
    scorer_source.parent.mkdir(parents=True, exist_ok=True)
    scorer_source.write_bytes(
        (SCRIPT.parents[1] / "sandbox" / "swebench" / "scorer_entrypoint.py").read_bytes()
    )
    scorer_source.chmod(0o600)
    scorer_entrypoint_sha256 = hashlib.sha256(scorer_source.read_bytes()).hexdigest()
    contexts = tmp_path / "contexts"
    contexts.mkdir(mode=0o700)
    if source_tasks is None:
        frozen_tasks: list[Mapping[str, Any]] = [
            {
                "instance_id": f"project__repo-{index}",
                "base_commit": f"{index + 1:040x}",
                "record_sha256": hashlib.sha256(
                    f"record-project__repo-{index}".encode()
                ).hexdigest(),
            }
            for index in range(task_count)
        ]
    else:
        frozen_tasks = list(source_tasks)
        task_count = len(frozen_tasks)
    instance_ids = [str(task["instance_id"]) for task in frozen_tasks]
    if selection_manifest_path is None:
        selection_manifest_path = tmp_path / "selection-manifest.json"
        selection: dict[str, Any] = {"task_count": task_count, "tasks": frozen_tasks}
        if scope == "verified":
            selection["verified_500_instance_ids"] = instance_ids
            selection["selection_policy"] = {"evidence_class": "held_out_capability"}
        selection_manifest_path.write_text(json.dumps(selection), encoding="utf-8")
        selection_manifest_path.chmod(0o600)
    if selection_manifest_sha256 is None:
        selection_manifest_sha256 = hashlib.sha256(selection_manifest_path.read_bytes()).hexdigest()
    host_test_spec_dir = tmp_path / "host-test-specs"
    host_test_spec_dir.mkdir(mode=0o700)
    images = []
    for task in frozen_tasks:
        instance_id = str(task["instance_id"])
        if scope in {"qualification", "verified"}:
            image_repository = f"swebench/sweb.eval.x86_64.{instance_id}"
            base_reference = (
                f"{image_repository}@sha256:{hashlib.sha256(instance_id.encode()).hexdigest()}"
            )
            source_image = f"{image_repository}:latest"
        else:
            base_reference = None
            source_image = f"local/{instance_id}@sha256:{'a' * 64}"
        for role in ("task", "grader"):
            context = _context(contexts, f"{instance_id}-{role}", base_reference)
            if role == "task":
                task_json = context / "task.json"
                task_json.write_text('{"task":"synthetic"}\n', encoding="utf-8")
                task_json.chmod(0o600)
            host_test_spec_path = None
            host_test_spec_sha256 = None
            scorer_revision = (
                hashlib.sha256(f"scorer-{instance_id}".encode()).hexdigest()
                if role == "grader"
                else None
            )
            trusted_tests_sha256 = None
            if role == "grader":
                grader_entrypoint = context / "racecraft-swebench-grade"
                grader_entrypoint.write_text(
                    "#!/usr/bin/env python3\nprint('fixture')\n", encoding="utf-8"
                )
                grader_entrypoint.chmod(0o500)
                dockerfile = context / "Dockerfile"
                dockerfile.write_text(
                    dockerfile.read_text(encoding="utf-8")
                    + "COPY --chown=0:0 --chmod=0444 test-spec-eval.sh "
                    "/opt/racecraft/grader/test-spec-eval.sh\n",
                    encoding="utf-8",
                )
                dockerfile.chmod(0o600)
                task_metadata = {
                    "instance_id": instance_id,
                    "image": source_image,
                    "repo": "example/project",
                    "version": "1.0",
                    "log_parser": "parse_log_pytest",
                    "eval_type": "pass_and_fail",
                }
                tests = {
                    "FAIL_TO_PASS": ["test_new"],
                    "PASS_TO_PASS": ["test_existing"],
                }
                eval_script = "#!/bin/bash\necho synthetic-test\n"
                grader_materials = {
                    "task.yaml": yaml.safe_dump(task_metadata).encode(),
                    "eval.sh": eval_script.encode(),
                    "test.patch": b"synthetic trusted test patch\n",
                    "tests.json": json.dumps(tests, separators=(",", ":")).encode(),
                }
                for name, content in grader_materials.items():
                    (context / name).write_bytes(content)
                    (context / name).chmod(0o600)
                trusted_tests_sha256 = builder._content_set_sha256(grader_materials)
                test_spec_fields = {
                    **task_metadata,
                    **tests,
                    "eval_script": eval_script,
                    "image_assets": None,
                }
                sidecar = {
                    "schema_version": 1,
                    "swebench_version": "5.0.2",
                    "instance_id": instance_id,
                    "source_record_sha256": task["record_sha256"],
                    "trusted_tests_sha256": trusted_tests_sha256,
                    "source_eval_script_sha256": hashlib.sha256(eval_script.encode()).hexdigest(),
                    "test_spec_sha256": hashlib.sha256(
                        json.dumps(
                            test_spec_fields,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                            allow_nan=False,
                        ).encode("utf-8")
                    ).hexdigest(),
                    "canonical_eval_script_b64": base64.b64encode(_CANONICAL_EVAL_SCRIPT).decode(
                        "ascii"
                    ),
                    "canonical_eval_script_sha256": hashlib.sha256(
                        _CANONICAL_EVAL_SCRIPT
                    ).hexdigest(),
                    "execution_eval_script_b64": base64.b64encode(_EXECUTION_EVAL_SCRIPT).decode(
                        "ascii"
                    ),
                    "execution_eval_script_sha256": hashlib.sha256(
                        _EXECUTION_EVAL_SCRIPT
                    ).hexdigest(),
                    "scorer_entrypoint_sha256": scorer_entrypoint_sha256,
                    "test_spec": test_spec_fields,
                }
                sidecar_path = host_test_spec_dir / f"{instance_id}.json"
                sidecar_raw = json.dumps(sidecar, sort_keys=True, separators=(",", ":")).encode()
                sidecar_path.write_bytes(sidecar_raw)
                sidecar_path.chmod(0o600)
                host_test_spec_path = str(sidecar_path)
                host_test_spec_sha256 = hashlib.sha256(sidecar_raw).hexdigest()
                generated_script = context / "test-spec-eval.sh"
                generated_script.write_bytes(_EXECUTION_EVAL_SCRIPT)
                generated_script.chmod(0o600)
            images.append(
                {
                    "instance_id": instance_id,
                    "base_commit": task["base_commit"],
                    "source_record_sha256": task["record_sha256"],
                    "role": role,
                    "tag": f"local/{instance_id}-{role}:{scope}",
                    "context": str(context),
                    "dockerfile": "Dockerfile",
                    "context_sha256": _tree_hash(context),
                    "official_scorer_revision_sha256": scorer_revision,
                    "trusted_tests_sha256": trusted_tests_sha256,
                    "host_test_spec_path": host_test_spec_path,
                    "host_test_spec_sha256": host_test_spec_sha256,
                }
            )
    execution_platform = platform or (
        "linux/amd64" if scope in {"qualification", "verified"} else "linux/arm64/v8"
    )
    payload = {
        "schema_version": 1,
        "scope": scope,
        "platform": execution_platform,
        "instance_ids": instance_ids,
        "selection_manifest_path": str(selection_manifest_path),
        "selection_manifest_sha256": selection_manifest_sha256,
        "resource_limits": {
            "memory_bytes": 1_073_741_824,
            "cpus": "1.0",
            "pids_limit": 64,
            "nofile_limit": 256,
            "tmpfs_bytes": 67_108_864,
        },
        "control_image_reference": control_reference,
        "control_image_digest": control_digest,
        "images": images,
    }
    spec = tmp_path / "build-spec.json"
    spec.write_text(json.dumps(payload), encoding="utf-8")
    spec.chmod(0o600)
    return spec


class FakeDocker:
    def __init__(
        self,
        *,
        control_image_id: str | None = None,
        control_repo_digests: Sequence[str] | None = None,
        container_env: Sequence[str] | None = None,
    ) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.removed: set[str] = set()
        self.containers: dict[str, str] = {}
        self.control_image_id = control_image_id
        self.control_repo_digests = control_repo_digests
        self.container_env = list(
            container_env
            if container_env is not None
            else ["HOME=/tmp/home", "PATH=/usr/local/bin:/usr/bin:/bin", "LANG=C.UTF-8"]
        )

    def __call__(
        self, args: Sequence[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        command = tuple(args)
        self.commands.append(command)
        if command[1:3] == ("info", "--format"):
            output = json.dumps({"OSType": "linux", "Architecture": "aarch64"})
        elif command[1:3] == ("image", "inspect"):
            reference = command[-1]
            is_control_inspect = not any(
                previous[1:3] == ("image", "inspect") for previous in self.commands[:-1]
            )
            platform = next(
                (value.split("=", 1)[1] for value in command if value.startswith("--platform=")),
                "linux/arm64/v8",
            )
            if "@sha256:" in reference:
                image_id = reference.rsplit("@", 1)[1]
            elif reference.startswith("sha256:"):
                image_id = reference
            else:
                image_id = "sha256:" + hashlib.sha256(reference.encode()).hexdigest()
            if is_control_inspect and self.control_image_id is not None:
                image_id = self.control_image_id
            repo_digests = (
                list(self.control_repo_digests)
                if is_control_inspect and self.control_repo_digests is not None
                else [reference]
                if is_control_inspect and "@sha256:" in reference
                else []
            )
            output = json.dumps(
                [
                    {
                        "Id": image_id,
                        "RepoDigests": repo_digests,
                        "Os": "linux",
                        "Architecture": "amd64" if platform == "linux/amd64" else "arm64",
                        "Variant": None if platform == "linux/amd64" else "v8",
                    }
                ]
            )
        elif command[1] == "create":
            name = next(value.split("=", 1)[1] for value in command if value.startswith("--name="))
            self.containers[name] = command[-2]
            output = name
        elif command[1] == "inspect":
            name = command[-1]
            if name in self.removed:
                if check:
                    raise subprocess.CalledProcessError(1, command)
                return subprocess.CompletedProcess(command, 1, "", "not found")
            output = json.dumps(
                [
                    {
                        "Image": self.containers[name],
                        "Config": {
                            "User": "65532:65532",
                            "Env": self.container_env,
                            "ExposedPorts": None,
                        },
                        "HostConfig": {
                            "NetworkMode": "none",
                            "ReadonlyRootfs": True,
                            "Privileged": False,
                            "CapDrop": ["ALL"],
                            "SecurityOpt": ["no-new-privileges"],
                            "PidsLimit": 64,
                            "Memory": 1_073_741_824,
                            "NanoCpus": 1_000_000_000,
                            "Binds": None,
                            "PortBindings": {},
                            "Ulimits": [{"Name": "nofile", "Soft": 256, "Hard": 256}],
                            "Tmpfs": {
                                "/tmp": "rw,noexec,nosuid,nodev,size=67108864"  # noqa: S108 - container-only tmpfs
                            },
                        },
                        "Mounts": [],
                    }
                ]
            )
        elif command[1:3] == ("rm", "--force"):
            self.removed.add(command[-1])
            output = command[-1]
        else:
            output = ""
        return subprocess.CompletedProcess(command, 0, output, "")


def test_builds_only_qualification_images_and_writes_private_attestation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path)
    state = tmp_path / "private-state"
    docker = FakeDocker()

    summary = builder.build_images(repo, state, spec, Path("/usr/bin/docker"), docker)

    assert summary == {
        "schema_version": 1,
        "scope": "qualification",
        "task_count": 2,
        "image_count": 4,
        "image_bindings_sha256": summary["image_bindings_sha256"],
        "ordered_instance_ids_sha256": summary["ordered_instance_ids_sha256"],
        "isolation_revision_sha256": builder._isolation_revision_sha256("linux/amd64"),
        "attestation_sha256": summary["attestation_sha256"],
    }
    builds = [
        command
        for command in docker.commands
        if command[1:4] == ("buildx", "build", "--network=none")
    ]
    control_inspect = next(
        command for command in docker.commands if command[1:3] == ("image", "inspect")
    )
    assert builder.CONTROL_PLATFORM == "linux/arm64/v8"
    assert "--platform=linux/arm64/v8" in control_inspect
    assert len(builds) == 4
    assert all("--platform=linux/amd64" in command for command in builds)
    assert all(
        "--load" in command
        and "--pull=false" in command
        and "--no-cache" in command
        and "--provenance=false" in command
        and "--sbom=false" in command
        for command in builds
    )
    creates = [command for command in docker.commands if command[1] == "create"]
    assert len(creates) == 4
    assert all("--platform=linux/amd64" in command for command in creates)
    assert all("--network=none" in command and "--read-only" in command for command in creates)
    assert all(
        "--user=65532:65532" in command and "--security-opt=no-new-privileges" in command
        for command in creates
    )
    assert all("--mount" not in command and "--publish" not in command for command in creates)
    attestations = list((state / "swebench-image-attestations").glob("*.json"))
    assert len(attestations) == 1
    assert stat.S_IMODE(attestations[0].stat().st_mode) == 0o600
    evidence = json.loads(attestations[0].read_bytes())
    grader_evidence = next(item for item in evidence["images"] if item["role"] == "grader")
    assert (
        grader_evidence["canonical_eval_script_sha256"]
        == hashlib.sha256(_CANONICAL_EVAL_SCRIPT).hexdigest()
    )
    assert (
        grader_evidence["execution_eval_script_sha256"]
        == hashlib.sha256(_EXECUTION_EVAL_SCRIPT).hexdigest()
    )
    assert grader_evidence["scorer_entrypoint_sha256"] == builder.SCORER_ENTRYPOINT_SHA256
    task_tag = "local/project__repo-0-task:qualification"
    grader_tag = "local/project__repo-0-grader:qualification"
    task_digest = "sha256:" + hashlib.sha256(task_tag.encode()).hexdigest()
    grader_digest = "sha256:" + hashlib.sha256(grader_tag.encode()).hexdigest()
    assert evidence["images"][0]["image_reference"] == f"local/project__repo-0-task@{task_digest}"
    assert evidence["images"][1]["image_reference"] == (
        f"local/project__repo-0-grader@{grader_digest}"
    )
    assert [(item["instance_id"], item["role"]) for item in evidence["images"]] == [
        ("project__repo-0", "task"),
        ("project__repo-0", "grader"),
        ("project__repo-1", "task"),
        ("project__repo-1", "grader"),
    ]
    assert all(item["runtime_contract"]["mounts"] == [] for item in evidence["images"])
    bindings_path = state / "swebench" / "qualification" / "image-bindings.json"
    bindings = json.loads(bindings_path.read_bytes())
    assert set(bindings) == {
        "schema_version",
        "benchmark",
        "mode",
        "task_count",
        "ordered_instance_ids_sha256",
        "control_image_reference",
        "control_image_digest",
        "isolation_revision_sha256",
        "execution_platform",
        "bindings",
    }
    assert (
        bindings["bindings"][0]["source_record_sha256"]
        == hashlib.sha256(b"record-project__repo-0").hexdigest()
    )
    grader_binding = next(
        item for item in bindings["bindings"] if item["instance_id"] == "project__repo-0"
    )
    assert (
        grader_binding["execution_eval_script_sha256"]
        == hashlib.sha256(_EXECUTION_EVAL_SCRIPT).hexdigest()
    )
    host_test_spec = state / bindings["bindings"][0]["host_test_spec_path"]
    assert stat.S_IMODE(host_test_spec.stat().st_mode) == 0o600
    assert (
        hashlib.sha256(host_test_spec.read_bytes()).hexdigest()
        == (bindings["bindings"][0]["host_test_spec_sha256"])
    )


@pytest.mark.parametrize(
    ("scope", "task_count", "message"),
    [("verified", 2, "exactly 500"), ("qualification", 11, "at most 10")],
)
def test_refuses_full_verified_or_oversized_qualification(
    tmp_path: Path, scope: str, task_count: int, message: str
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path, scope=scope, task_count=task_count)

    with pytest.raises(builder.BuildError, match=message):
        builder.build_images(repo, tmp_path / "state", spec, Path("docker"), FakeDocker())


def test_qualification_refuses_native_arm64_before_docker(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path, platform="linux/arm64/v8")
    docker = FakeDocker()

    with pytest.raises(builder.BuildError, match="qualification platform must be linux/amd64"):
        builder.build_images(repo, tmp_path / "state", spec, Path("docker"), docker)

    assert docker.commands == []


@pytest.mark.parametrize("profile_name", ["swebench-qualification.yaml", "swebench-verified.yaml"])
def test_swebench_profiles_bind_amd64_task_platform(profile_name: str) -> None:
    profile_path = SCRIPT.parents[1] / "configs" / "profiles" / profile_name
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))

    assert profile["swebench"]["platform"] == builder.QUALIFICATION_PLATFORM == "linux/amd64"


def test_synthetic_scope_preserves_native_arm64(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path, scope="synthetic", task_count=1)
    docker = FakeDocker()

    summary = builder.build_images(repo, tmp_path / "state", spec, Path("docker"), docker)

    assert summary["scope"] == "synthetic"
    builds = [command for command in docker.commands if command[1:3] == ("buildx", "build")]
    creates = [command for command in docker.commands if command[1] == "create"]
    assert all("--platform=linux/arm64/v8" in command for command in builds)
    assert all("--platform=linux/arm64/v8" in command for command in creates)


def test_refuses_context_drift_before_docker_is_called(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path)
    payload: dict[str, Any] = json.loads(spec.read_bytes())
    payload["images"][0]["context_sha256"] = "0" * 64
    spec.write_text(json.dumps(payload), encoding="utf-8")
    docker = FakeDocker()

    with pytest.raises(builder.BuildError, match="context fingerprint"):
        builder.build_images(repo, tmp_path / "state", spec, Path("docker"), docker)

    assert docker.commands == []


def test_refuses_generated_execution_script_drift_before_docker(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path)
    payload: dict[str, Any] = json.loads(spec.read_bytes())
    grader_image = payload["images"][1]
    context = Path(grader_image["context"])
    (context / "test-spec-eval.sh").write_bytes(b"#!/bin/bash\nexit 0\n")
    (context / "test-spec-eval.sh").chmod(0o600)
    grader_image["context_sha256"] = _tree_hash(context)
    spec.write_text(json.dumps(payload), encoding="utf-8")
    docker = FakeDocker()

    with pytest.raises(builder.BuildError, match="generated eval script differs from frozen bytes"):
        builder.build_images(repo, tmp_path / "state", spec, Path("docker"), docker)

    assert docker.commands == []


def test_refuses_unexpected_task_context_files_before_docker(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path)
    payload: dict[str, Any] = json.loads(spec.read_bytes())
    task_image = payload["images"][0]
    context = Path(task_image["context"])
    unexpected = context / "host-test-spec.json"
    unexpected.write_text("{}\n", encoding="utf-8")
    unexpected.chmod(0o600)
    task_image["context_sha256"] = _tree_hash(context)
    spec.write_text(json.dumps(payload), encoding="utf-8")
    docker = FakeDocker()

    with pytest.raises(builder.BuildError, match="only Dockerfile and task.json"):
        builder.build_images(repo, tmp_path / "state", spec, Path("docker"), docker)

    assert docker.commands == []


@pytest.mark.parametrize("image_index", [0, 1], ids=["task", "grader"])
def test_refuses_host_test_spec_inside_every_role_context(tmp_path: Path, image_index: int) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path)
    payload: dict[str, Any] = json.loads(spec.read_bytes())
    image = payload["images"][image_index]
    context = Path(image["context"])
    sidecar_in_context = context / "Dockerfile"
    image["host_test_spec_path"] = str(sidecar_in_context)
    image["host_test_spec_sha256"] = hashlib.sha256(sidecar_in_context.read_bytes()).hexdigest()
    spec.write_text(json.dumps(payload), encoding="utf-8")
    docker = FakeDocker()

    with pytest.raises(builder.BuildError, match="host TestSpec may not be included"):
        builder.build_images(repo, tmp_path / "state", spec, Path("docker"), docker)

    assert docker.commands == []


def test_refuses_shared_task_and_grader_context_before_docker(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path)
    payload: dict[str, Any] = json.loads(spec.read_bytes())
    task_image, grader_image = payload["images"][:2]
    grader_image["context"] = task_image["context"]
    grader_image["context_sha256"] = task_image["context_sha256"]
    spec.write_text(json.dumps(payload), encoding="utf-8")
    docker = FakeDocker()

    with pytest.raises(builder.BuildError, match="image build contexts must be distinct"):
        builder.build_images(repo, tmp_path / "state", spec, Path("docker"), docker)

    assert docker.commands == []


def test_refuses_context_inside_git_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    context = _context(repo, "task-context")
    spec = _spec(tmp_path)
    payload: dict[str, Any] = json.loads(spec.read_bytes())
    payload["images"][0]["context"] = str(context)
    payload["images"][0]["context_sha256"] = _tree_hash(context)
    spec.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(builder.BuildError, match="outside the Git checkout"):
        builder.build_images(repo, tmp_path / "state", spec, Path("docker"), FakeDocker())


def test_refuses_symlinked_private_artifact_directory_before_build(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    target = tmp_path / "redirected"
    target.mkdir(mode=0o700)
    (state / "swebench").symlink_to(target, target_is_directory=True)
    docker = FakeDocker()

    with pytest.raises(builder.BuildError, match="artifact directory is unsafe"):
        builder.build_images(repo, state, spec, Path("docker"), docker)

    assert docker.commands == []


def test_refuses_runtime_attestation_mismatch_and_still_cleans_up(tmp_path: Path) -> None:
    class UnsafeDocker(FakeDocker):
        def __call__(
            self, args: Sequence[str], *, check: bool = True
        ) -> subprocess.CompletedProcess[str]:
            result = super().__call__(args, check=check)
            if tuple(args)[1] == "inspect" and result.returncode == 0:
                payload = json.loads(result.stdout)
                payload[0]["HostConfig"]["NetworkMode"] = "bridge"
                return subprocess.CompletedProcess(tuple(args), 0, json.dumps(payload), "")
            return result

    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path)
    docker = UnsafeDocker()

    with pytest.raises(builder.BuildError, match="runtime safety contract"):
        builder.build_images(repo, tmp_path / "state", spec, Path("docker"), docker)

    assert any(command[1:3] == ("rm", "--force") for command in docker.commands)


def test_refuses_unpinned_base_or_dockerfile_add(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path)
    payload: dict[str, Any] = json.loads(spec.read_bytes())
    context = Path(payload["images"][0]["context"])
    dockerfile = context / "Dockerfile"
    dockerfile.write_text("FROM python:3.11\nADD https://example.invalid/tool /tool\n")
    dockerfile.chmod(0o600)
    payload["images"][0]["context_sha256"] = _tree_hash(context)
    spec.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(builder.BuildError, match="digest-pinned"):
        builder.build_images(repo, tmp_path / "state", spec, Path("docker"), FakeDocker())


def test_emitted_binding_manifest_passes_current_readiness_validation(tmp_path: Path) -> None:
    from local_evals.swebench import inspect_swebench_readiness

    contract = _load_swebench_contract()

    repo = Path.cwd()
    state = tmp_path / "private-state"
    state.mkdir(mode=0o700)
    profile = contract._profile(repo, state, count=2)
    manifest_path = Path(profile["manifest_path"])
    manifest_path.chmod(0o600)
    manifest = json.loads(manifest_path.read_bytes())
    Path(profile["image_bindings_path"]).unlink()
    for source_sidecar in (state / "swebench" / "qualification" / "host-test-specs").glob("*.json"):
        source_sidecar.unlink()
    build_root = tmp_path / "private-build"
    build_root.mkdir(mode=0o700)
    spec = _spec(
        build_root,
        source_tasks=manifest["tasks"],
        selection_manifest_path=manifest_path,
        selection_manifest_sha256=profile["manifest_sha256"],
        control_reference=profile["control_image_reference"],
        control_digest=profile["control_image_digest"],
    )

    summary = builder.build_images(repo, state, spec, Path("docker"), FakeDocker())
    profile["image_bindings_path"] = str(
        state / "swebench" / "qualification" / "image-bindings.json"
    )
    profile["image_bindings_sha256"] = summary["image_bindings_sha256"]
    profile["image_bindings_task_count"] = summary["task_count"]
    profile["image_bindings_ordered_instance_ids_sha256"] = summary["ordered_instance_ids_sha256"]

    report = inspect_swebench_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=contract._runtime(contract._FakeRunner()),
    )

    assert report["status"] == "ready"
    assert report["blockers"] == []


def test_control_image_accepts_matching_repo_digest_with_different_config_id(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path)
    docker = FakeDocker(control_image_id=f"sha256:{'d' * 64}")

    summary = builder.build_images(repo, tmp_path / "state", spec, Path("docker"), docker)

    assert summary["image_count"] == 4


def test_control_image_config_id_does_not_replace_missing_repo_digest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path)
    docker = FakeDocker(control_image_id=_CONTROL_DIGEST, control_repo_digests=[])

    with pytest.raises(builder.BuildError, match="control image digest"):
        builder.build_images(repo, tmp_path / "state", spec, Path("docker"), docker)

    assert not any(command[1:3] == ("buildx", "build") for command in docker.commands)


def test_control_image_rejects_mismatched_repo_digest_even_with_matching_config_id(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path)
    mismatched_reference = f"local/swebench-control@sha256:{'d' * 64}"
    docker = FakeDocker(
        control_image_id=_CONTROL_DIGEST,
        control_repo_digests=[mismatched_reference],
    )

    with pytest.raises(builder.BuildError, match="control image digest"):
        builder.build_images(repo, tmp_path / "state", spec, Path("docker"), docker)

    assert not any(command[1:3] == ("buildx", "build") for command in docker.commands)


def test_builds_verified_scope_and_writes_verified_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(builder, "VERIFIED_TASK_COUNT", 2)
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path, scope="verified", task_count=2)
    state = tmp_path / "private-state"
    docker = FakeDocker()

    summary = builder.build_images(repo, state, spec, Path("docker"), docker)

    assert summary["scope"] == "verified"
    assert summary["task_count"] == 2
    assert summary["image_count"] == 4
    assert summary["isolation_revision_sha256"] == builder._isolation_revision_sha256("linux/amd64")
    builds = [command for command in docker.commands if command[1:3] == ("buildx", "build")]
    creates = [command for command in docker.commands if command[1] == "create"]
    assert len(builds) == 4
    assert all("--platform=linux/amd64" in command for command in builds + creates)
    bindings = json.loads((state / "swebench" / "verified" / "image-bindings.json").read_bytes())
    assert bindings["mode"] == "verified"
    assert bindings["execution_platform"] == "linux/amd64"
    assert [item["instance_id"] for item in bindings["bindings"]] == [
        "project__repo-0",
        "project__repo-1",
    ]
    for item in bindings["bindings"]:
        relative = f"swebench/verified/host-test-specs/{item['instance_id']}.json"
        assert item["host_test_spec_path"] == relative
        host_test_spec = state / relative
        assert stat.S_IMODE(host_test_spec.stat().st_mode) == 0o600
        assert (
            hashlib.sha256(host_test_spec.read_bytes()).hexdigest()
            == (item["host_test_spec_sha256"])
        )
    assert not (state / "swebench" / "qualification").exists()


def test_verified_scope_refuses_selection_that_is_not_the_ordered_verified_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(builder, "VERIFIED_TASK_COUNT", 2)
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path, scope="verified", task_count=2)
    payload = json.loads(spec.read_bytes())
    selection_path = Path(payload["selection_manifest_path"])
    selection = json.loads(selection_path.read_bytes())
    selection["verified_500_instance_ids"].reverse()
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    payload["selection_manifest_sha256"] = hashlib.sha256(selection_path.read_bytes()).hexdigest()
    spec.write_text(json.dumps(payload), encoding="utf-8")
    docker = FakeDocker()

    with pytest.raises(builder.BuildError, match="ordered Verified manifest"):
        builder.build_images(repo, tmp_path / "state", spec, Path("docker"), docker)

    assert docker.commands == []


def test_verified_grader_must_bind_host_test_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(builder, "VERIFIED_TASK_COUNT", 2)
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path, scope="verified", task_count=2)
    payload = json.loads(spec.read_bytes())
    payload["images"][1]["host_test_spec_path"] = None
    payload["images"][1]["host_test_spec_sha256"] = None
    spec.write_text(json.dumps(payload), encoding="utf-8")
    docker = FakeDocker()

    with pytest.raises(builder.BuildError, match="must bind a host-only TestSpec"):
        builder.build_images(repo, tmp_path / "state", spec, Path("docker"), docker)

    assert docker.commands == []


class _LabeledDocker(FakeDocker):
    """Report an existing local tag that carries a chosen build-inputs label."""

    def __init__(self, labels_by_tag: Mapping[str, str]) -> None:
        super().__init__()
        self.labels_by_tag = dict(labels_by_tag)

    def __call__(
        self, args: Sequence[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        result = super().__call__(args, check=check)
        command = tuple(args)
        if command[1:3] == ("image", "inspect") and command[-1] in self.labels_by_tag:
            payload = json.loads(result.stdout)
            payload[0]["Config"] = {
                "Labels": {builder.BUILD_INPUTS_LABEL: self.labels_by_tag[command[-1]]}
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        return result


def test_reuse_skips_only_tags_labeled_with_identical_build_inputs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path)
    payload = json.loads(spec.read_bytes())
    task_image, grader_image = payload["images"][:2]
    docker = _LabeledDocker(
        {
            task_image["tag"]: task_image["context_sha256"],
            grader_image["tag"]: "0" * 64,
        }
    )
    state = tmp_path / "state"

    builder.build_images(repo, state, spec, Path("docker"), docker, reuse_existing_images=True)

    built_tags = {
        command[command.index("--tag") + 1]
        for command in docker.commands
        if command[1:3] == ("buildx", "build")
    }
    all_tags = {image["tag"] for image in payload["images"]}
    assert built_tags == all_tags - {task_image["tag"]}
    evidence = json.loads(next((state / "swebench-image-attestations").glob("*.json")).read_bytes())
    reused = {
        (item["instance_id"], item["role"]): item["reused_existing_image"]
        for item in evidence["images"]
    }
    assert reused.pop((task_image["instance_id"], "task")) is True
    assert not any(reused.values())
    assert len([command for command in docker.commands if command[1] == "create"]) == 4


def test_builds_label_inputs_and_rebuild_by_default(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _spec(tmp_path)
    payload = json.loads(spec.read_bytes())
    docker = _LabeledDocker({image["tag"]: image["context_sha256"] for image in payload["images"]})

    builder.build_images(repo, tmp_path / "state", spec, Path("docker"), docker)

    builds = [command for command in docker.commands if command[1:3] == ("buildx", "build")]
    assert len(builds) == 4
    for command, image in zip(builds, payload["images"], strict=True):
        label = command[command.index("--label") + 1]
        assert label == f"{builder.BUILD_INPUTS_LABEL}={image['context_sha256']}"


def test_emitted_verified_bindings_pass_current_readiness_validation(tmp_path: Path) -> None:
    from local_evals.swebench import inspect_swebench_readiness

    contract = _load_swebench_contract()

    repo = Path.cwd()
    state = tmp_path / "private-state"
    state.mkdir(mode=0o700)
    profile = contract._profile(repo, state, mode="verified")
    manifest_path = Path(profile["manifest_path"])
    manifest_path.chmod(0o600)
    manifest = json.loads(manifest_path.read_bytes())
    Path(profile["image_bindings_path"]).unlink()
    for source_sidecar in (state / "swebench" / "verified" / "host-test-specs").glob("*.json"):
        source_sidecar.unlink()
    build_root = tmp_path / "private-build"
    build_root.mkdir(mode=0o700)
    spec = _spec(
        build_root,
        scope="verified",
        source_tasks=manifest["tasks"],
        selection_manifest_path=manifest_path,
        selection_manifest_sha256=profile["manifest_sha256"],
        control_reference=profile["control_image_reference"],
        control_digest=profile["control_image_digest"],
    )

    summary = builder.build_images(repo, state, spec, Path("docker"), FakeDocker())
    assert summary["task_count"] == 500
    profile["image_bindings_path"] = str(state / "swebench" / "verified" / "image-bindings.json")
    profile["image_bindings_sha256"] = summary["image_bindings_sha256"]
    profile["image_bindings_task_count"] = summary["task_count"]
    profile["image_bindings_ordered_instance_ids_sha256"] = summary["ordered_instance_ids_sha256"]

    report = inspect_swebench_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=contract._runtime(contract._FakeRunner()),
    )

    assert report["status"] == "ready"
    assert report["blockers"] == []
