from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import stat
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

SCRIPT = Path(__file__).parents[2] / "scripts" / "prepare_swebench_qualification_contexts.py"
BUILDER_SCRIPT = Path(__file__).parents[2] / "scripts" / "build_swebench_images.py"
TASK_REPOSITORY_REVISION = "3d07b464b7b311a0cbfb5ed5b2d8a3b96f84a33d"
PARENT_DATASET_REVISION = "c6fe717fd7a4c3ac1daa4055a4fd082c6a1d28a2"
VERIFIED_DATASET_REVISION = "78f471bf655a3137b2e8a75af1501690ec009ec3"
_CANONICAL_EVAL_SCRIPT = (
    b"#!/bin/bash\n"
    b"set -uxo pipefail\n"
    b"pytest -q tests/test_frozen.py\n"
    b"SWEBENCH_TEST_EXIT_CODE=$?\n"
    b": '>>>>> End Test Output'\n"
    b'echo ">>>>> Test Exit Code: $SWEBENCH_TEST_EXIT_CODE"\n'
    b"git checkout -- tests/test_frozen.py\n"
)


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prepare_swebench_qualification_contexts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preparer = _load_script()


def _load_builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "qualification_context_builder_contract", BUILDER_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


class FakeControlRunner:
    def __init__(
        self,
        control_reference: str,
        *,
        repo_digests: list[str] | None = None,
        corrupt_prepare_identity: bool = False,
    ) -> None:
        self.control_reference = control_reference
        self.repo_digests = [control_reference] if repo_digests is None else repo_digests
        self.corrupt_prepare_identity = corrupt_prepare_identity
        self.commands: list[tuple[str, ...]] = []

    def __call__(
        self, args: tuple[str, ...], input_bytes: bytes | None
    ) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(args)
        if args[1:3] == ("image", "inspect"):
            response: object = [
                {
                    "Id": f"sha256:{'f' * 64}",
                    "RepoDigests": self.repo_digests,
                    "Os": "linux",
                    "Architecture": "arm64",
                    "Variant": "v8",
                }
            ]
        else:
            assert args[1] == "run"
            assert input_bytes is not None
            request = json.loads(input_bytes)
            test_spec = request["test_spec"]
            test_spec_sha256 = _sha256(
                json.dumps(
                    test_spec,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            )
            response = {
                "schema_version": 1,
                "action": "prepare",
                "scorer_status": "prepared",
                "error": None,
                "swebench_version": "5.0.2",
                "instance_id": request["instance_id"],
                "test_spec_sha256": (
                    "0" * 64 if self.corrupt_prepare_identity else test_spec_sha256
                ),
                "eval_script_b64": base64.b64encode(_CANONICAL_EVAL_SCRIPT).decode("ascii"),
                "eval_script_sha256": _sha256(_CANONICAL_EVAL_SCRIPT),
                "scorer_entrypoint_sha256": preparer.SCORER_ENTRYPOINT_SHA256,
                "patch_sha256": None,
                "grading_log_sha256": None,
                "host_cleanup_confirmed": None,
                "resolved": None,
                "report": None,
            }
        return subprocess.CompletedProcess(
            args, 0, json.dumps(response, separators=(",", ":")).encode(), b""
        )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _private_json(path: Path, value: object) -> str:
    path.write_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    path.chmod(0o600)
    return _sha256(path.read_bytes())


def _record(index: int) -> dict[str, Any]:
    instance_id = f"project__repo-{index:02d}"
    return {
        "instance_id": instance_id,
        "repo": "project/repo",
        "base_commit": f"{index + 1:040x}",
        "environment_setup_commit": f"{index + 101:040x}",
        "problem_statement": f"Repair task {index}",
        "patch": f"training patch {index}",
        "test_patch": f"test patch {index}\n",
        "eval_script": f"#!/bin/bash\necho task-{index}\n",
        "FAIL_TO_PASS": [f"tests/test_{index}.py::test_fixed"],
        "PASS_TO_PASS": [f"tests/test_{index}.py::test_existing"],
        "created_at": "2024-01-01T00:00:00Z",
        "difficulty": "",
        "eval_type": "pass_and_fail",
        "hints_text": "",
        "image": f"swebench/sweb.eval.x86_64.project_repo-{index:02d}:latest",
        "log_parser": "parse_log_pytest",
        "version": "1",
    }


def _fixture(tmp_path: Path) -> dict[str, Any]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    scorer_entrypoint = repo / "sandbox" / "swebench" / "scorer_entrypoint.py"
    scorer_entrypoint.parent.mkdir(parents=True)
    scorer_entrypoint.write_bytes(
        (SCRIPT.parents[1] / "sandbox" / "swebench" / "scorer_entrypoint.py").read_bytes()
    )
    scorer_entrypoint.chmod(0o600)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    records = [_record(index) for index in range(10)]
    records_path = private / "qualification-records.jsonl"
    records_path.write_bytes(
        b"".join(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            for record in records
        )
    )
    records_path.chmod(0o600)
    tasks = [
        {
            "instance_id": record["instance_id"],
            "repository": record["repo"],
            "base_commit": record["base_commit"],
            "problem_statement_sha256": _sha256(record["problem_statement"].encode()),
            "record_sha256": _sha256(
                json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
            ),
        }
        for record in records
    ]
    verified_ids = [f"verified__repo-{index:03d}" for index in range(500)]
    verified_ids_sha = _sha256(json.dumps(verified_ids, separators=(",", ":")).encode())
    license_receipt = private / "license.txt"
    license_receipt.write_text("approved for private local qualification\n", encoding="utf-8")
    license_receipt.chmod(0o600)
    license_sha = _sha256(license_receipt.read_bytes())
    qualification_manifest = private / "qualification-manifest.json"
    qualification_sha = _private_json(
        qualification_manifest,
        {
            "schema_version": 1,
            "benchmark": "swebench_verified",
            "dataset_id": "SWE-bench/SWE-bench",
            "dataset_revision": PARENT_DATASET_REVISION,
            "split": "test",
            "task_count": 10,
            "tasks": tasks,
            "records_path": "swebench/qualification/records.jsonl",
            "records_sha256": _sha256(records_path.read_bytes()),
            "license_authorized": True,
            "license_authorization_revision": license_sha,
            "frozen_before_tuning": True,
            "verified_500_instance_ids": verified_ids,
            "verified_500_ids_sha256": verified_ids_sha,
            "ordered_instance_ids_sha256": _sha256(
                json.dumps([task["instance_id"] for task in tasks], separators=(",", ":")).encode()
            ),
            "source_sha256": "a" * 64,
            "selection_policy": {
                "schema_version": 1,
                "algorithm": "sha256_utf8_seed_nul_instance_id",
                "seed": "racecraft-swebench-qualification-v1",
                "candidate_dataset_id": "SWE-bench/SWE-bench",
                "candidate_split": "test",
                "exclusion": "swebench_verified_500_instance_ids",
                "evidence_class": "qualification_only_non_capability",
                "output_order": "instance_id_ascending",
                "rank_tiebreaker": "instance_id_ascending",
            },
        },
    )
    verified_manifest = private / "verified-manifest.json"
    verified_sha = _private_json(
        verified_manifest,
        {
            "schema_version": 1,
            "benchmark": "swebench_verified",
            "dataset_id": "SWE-bench/SWE-bench_Verified",
            "dataset_revision": VERIFIED_DATASET_REVISION,
            "split": "test",
            "task_count": 500,
            "tasks": [
                {
                    "instance_id": instance_id,
                    "repository": "verified/repo",
                    "base_commit": "f" * 40,
                    "problem_statement_sha256": "b" * 64,
                    "record_sha256": "c" * 64,
                }
                for instance_id in verified_ids
            ],
            "verified_500_instance_ids": verified_ids,
            "verified_500_ids_sha256": verified_ids_sha,
        },
    )

    task_repo = private / "swe-bench-tasks"
    tasks_root = task_repo / "tasks"
    tasks_root.mkdir(parents=True)
    base_images = []
    for record in records:
        task_dir = tasks_root / record["instance_id"]
        task_dir.mkdir()
        task_yaml = {
            "base_commit": record["base_commit"],
            "created_at": record["created_at"],
            "datasets": ["SWE-bench/SWE-bench"],
            "environment_setup_commit": record["environment_setup_commit"],
            "eval_type": record["eval_type"],
            "image": record["image"],
            "instance_id": record["instance_id"],
            "log_parser": record["log_parser"],
            "repo": record["repo"],
            "split": "test",
            "version": record["version"],
        }
        (task_dir / "task.yaml").write_text(yaml.safe_dump(task_yaml), encoding="utf-8")
        (task_dir / "Dockerfile").write_text(
            "FROM --platform=linux/amd64 ubuntu:jammy\n", encoding="utf-8"
        )
        (task_dir / "eval.sh").write_text(record["eval_script"], encoding="utf-8")
        (task_dir / "test.patch").write_text(record["test_patch"], encoding="utf-8")
        (task_dir / "tests.json").write_text(
            json.dumps(
                {
                    "FAIL_TO_PASS": record["FAIL_TO_PASS"],
                    "PASS_TO_PASS": record["PASS_TO_PASS"],
                }
            ),
            encoding="utf-8",
        )
        digest = f"sha256:{_sha256(record['instance_id'].encode())}"
        base_images.append(
            {
                "instance_id": record["instance_id"],
                "image_reference": f"{record['image'].rsplit(':', 1)[0]}@{digest}",
                "image_digest": digest,
                "dockerfile_sha256": _sha256((task_dir / "Dockerfile").read_bytes()),
            }
        )

    validation = private / "dataset-check.json"
    validation_sha = _private_json(
        validation,
        {
            "schema_version": 1,
            "command": "swebench dataset check",
            "swebench_version": "5.0.2",
            "task_repository_revision": TASK_REPOSITORY_REVISION,
            "status": "passed",
            "checked_instance_ids": [record["instance_id"] for record in records],
            "output_sha256": "d" * 64,
        },
    )
    images = private / "base-images.json"
    images_sha = _private_json(
        images,
        {
            "schema_version": 1,
            "platform": "linux/amd64",
            "task_repository_revision": TASK_REPOSITORY_REVISION,
            "images": base_images,
        },
    )
    grader = private / "racecraft-swebench-grade"
    grader.write_text("#!/usr/bin/env python3\nprint('fixture')\n", encoding="utf-8")
    grader.chmod(0o500)

    control_reference = f"local/swebench-control@sha256:{'e' * 64}"
    return {
        "repo_root": repo,
        "output_root": private / "prepared",
        "qualification_manifest": qualification_manifest,
        "qualification_manifest_sha256": qualification_sha,
        "qualification_records": records_path,
        "verified_manifest": verified_manifest,
        "verified_manifest_sha256": verified_sha,
        "task_repository": task_repo,
        "dataset_check_evidence": validation,
        "dataset_check_evidence_sha256": validation_sha,
        "base_image_evidence": images,
        "base_image_evidence_sha256": images_sha,
        "license_authorization_receipt": license_receipt,
        "grader_entrypoint": grader,
        "grader_entrypoint_sha256": _sha256(grader.read_bytes()),
        "control_image_reference": control_reference,
        "control_image_digest": f"sha256:{'e' * 64}",
        "control_runner": FakeControlRunner(control_reference),
        "resource_limits": {
            "memory_bytes": 17_179_869_184,
            "cpus": "4.0",
            "pids_limit": 512,
            "nofile_limit": 1024,
            "tmpfs_bytes": 268_435_456,
        },
        "revision_reader": lambda _path: TASK_REPOSITORY_REVISION,
    }


def _with_prior_qualification_exclusion(inputs: dict[str, Any]) -> dict[str, Any]:
    current = json.loads(inputs["qualification_manifest"].read_bytes())
    prior_ids = [f"prior__repo-{index:02d}" for index in range(10)]
    prior_manifest = json.loads(inputs["qualification_manifest"].read_bytes())
    for task, instance_id in zip(prior_manifest["tasks"], prior_ids, strict=True):
        task["instance_id"] = instance_id
    prior_manifest["ordered_instance_ids_sha256"] = _sha256(
        json.dumps(prior_ids, separators=(",", ":")).encode()
    )
    prior_manifest_path = inputs["qualification_manifest"].parent / "prior-manifest.json"
    prior_manifest_sha = _private_json(prior_manifest_path, prior_manifest)
    exclusions_path = inputs["qualification_manifest"].parent / "prior-exclusions.json"
    exclusions_sha = _private_json(
        exclusions_path,
        {
            "schema_version": 1,
            "prior_manifest_sha256": prior_manifest_sha,
            "instance_ids": prior_ids,
        },
    )
    current["selection_policy"]["exclusion"] = (
        "swebench_verified_500_and_prior_qualification_instance_ids"
    )
    current["selection_policy"]["prior_qualification_manifest_sha256"] = prior_manifest_sha
    current["selection_policy"]["prior_qualification_exclusions_sha256"] = exclusions_sha
    inputs["qualification_manifest_sha256"] = _private_json(
        inputs["qualification_manifest"], current
    )
    inputs["prior_qualification_manifest"] = prior_manifest_path
    inputs["prior_qualification_exclusions"] = exclusions_path
    return inputs


def test_prepares_exact_private_amd64_task_and_grader_contexts(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)

    summary = preparer.prepare_qualification_contexts(**inputs)

    assert summary["task_count"] == 10
    assert summary["image_count"] == 20
    assert summary["platform"] == "linux/amd64"
    assert summary["task_repository_revision"] == TASK_REPOSITORY_REVISION
    spec_path = inputs["output_root"] / "build-spec.json"
    assert stat.S_IMODE(spec_path.stat().st_mode) == 0o600
    spec = json.loads(spec_path.read_bytes())
    assert spec["scope"] == "qualification"
    assert spec["platform"] == "linux/amd64"
    assert len(spec["images"]) == 20
    assert spec["selection_manifest_path"] == str(inputs["qualification_manifest"].resolve())
    host_test_specs = inputs["output_root"] / "host-test-specs"
    assert stat.S_IMODE(host_test_specs.stat().st_mode) == 0o700
    provenance = json.loads((inputs["output_root"] / "provenance.json").read_bytes())
    assert provenance["platform"] == "linux/amd64"
    assert provenance["emulation_required_on_apple_silicon"] is True
    assert provenance["control_image_reference"] == inputs["control_image_reference"]
    assert provenance["control_image_digest"] == inputs["control_image_digest"]
    assert provenance["control_platform"] == "linux/arm64/v8"
    assert provenance["scorer_entrypoint_sha256"] == preparer.SCORER_ENTRYPOINT_SHA256
    control_runner = inputs["control_runner"]
    assert len(control_runner.commands) == 11
    assert control_runner.commands[0][1:3] == ("image", "inspect")
    assert all(
        "--network=none" in command
        and "--read-only" in command
        and "--mount" not in command
        and command[-2:] == ("-B", preparer.SCORER_ENTRYPOINT_PATH)
        for command in control_runner.commands[1:]
    )
    validated_spec, validated_sha = builder._validate_spec(
        inputs["repo_root"].resolve(), spec_path.resolve()
    )
    assert validated_spec["platform"] == "linux/amd64"
    assert validated_sha == summary["build_spec_sha256"]
    for image in spec["images"]:
        context = Path(image["context"])
        assert stat.S_IMODE(context.stat().st_mode) == 0o700
        assert image["context_sha256"] == preparer._tree_sha256(context)
        dockerfile = (context / "Dockerfile").read_text(encoding="utf-8")
        assert "FROM --platform=linux/amd64" in dockerfile
        record = _record(int(image["instance_id"][-2:]))
        base_reference = (
            f"{record['image'].rsplit(':', 1)[0]}@sha256:{_sha256(record['instance_id'].encode())}"
        )
        assert dockerfile.startswith(f"FROM --platform=linux/amd64 {base_reference}\n")
        lines = dockerfile.splitlines()
        assert lines[1] == "RUN chown -R 65532:65532 /testbed"
        assert lines.count("RUN chown -R 65532:65532 /testbed") == 1
        activation = "RUN install -m 0444 /root/.bashrc /opt/racecraft/bashrc"
        assert (activation in lines) is (image["role"] == "task")
        if image["role"] == "task":
            assert lines.index(activation) > 1
        if image["role"] == "task":
            assert image["official_scorer_revision_sha256"] is None
            assert image["trusted_tests_sha256"] is None
            assert image["host_test_spec_path"] is None
            assert image["host_test_spec_sha256"] is None
            assert sorted(path.name for path in context.iterdir()) == ["Dockerfile", "task.json"]
            task = json.loads((context / "task.json").read_bytes())
            assert set(task) == {"instance_id", "problem_statement"}
        else:
            assert image["official_scorer_revision_sha256"] is not None
            assert image["trusted_tests_sha256"] is not None
            assert image["host_test_spec_sha256"] is not None
            host_spec_path = Path(image["host_test_spec_path"])
            assert host_spec_path == host_test_specs / f"{image['instance_id']}.json"
            assert stat.S_IMODE(host_spec_path.stat().st_mode) == 0o600
            host_spec = json.loads(host_spec_path.read_bytes())
            assert host_spec["instance_id"] == image["instance_id"]
            assert host_spec["source_record_sha256"] == image["source_record_sha256"]
            assert host_spec["trusted_tests_sha256"] == image["trusted_tests_sha256"]
            assert host_spec["source_eval_script_sha256"] == _sha256(
                _record(int(image["instance_id"][-2:]))["eval_script"].encode()
            )
            assert host_spec["test_spec_sha256"] == _sha256(
                preparer._canonical(host_spec["test_spec"])
            )
            assert host_spec["canonical_eval_script_sha256"] == _sha256(
                base64.b64decode(host_spec["canonical_eval_script_b64"], validate=True)
            )
            execution_script = base64.b64decode(
                host_spec["execution_eval_script_b64"], validate=True
            )
            assert execution_script == _CANONICAL_EVAL_SCRIPT + (
                b'exit "$SWEBENCH_TEST_EXIT_CODE"\n'
            )
            assert host_spec["execution_eval_script_sha256"] == _sha256(execution_script)
            assert host_spec["scorer_entrypoint_sha256"] == preparer.SCORER_ENTRYPOINT_SHA256
            assert "patch" not in host_spec and "test_patch" not in host_spec
            assert "patch" not in host_spec["test_spec"]
            assert "test_patch" not in host_spec["test_spec"]
            assert (
                host_spec["test_spec"]["eval_script"]
                == _record(int(image["instance_id"][-2:]))["eval_script"]
            )
            assert {
                "Dockerfile",
                "eval.sh",
                "racecraft-swebench-grade",
                "task.yaml",
                "test-spec-eval.sh",
                "test.patch",
                "tests.json",
            } == {path.name for path in context.iterdir()}
            generated_copy = (
                "COPY --chown=0:0 --chmod=0444 test-spec-eval.sh "
                "/opt/racecraft/grader/test-spec-eval.sh"
            )
            assert generated_copy in dockerfile
            assert (context / "test-spec-eval.sh").read_bytes() == execution_script
    assert not any(
        inputs["repo_root"].resolve() in path.parents for path in inputs["output_root"].rglob("*")
    )


def test_prepares_selection_with_verified_prior_exclusion_evidence(tmp_path: Path) -> None:
    inputs = _with_prior_qualification_exclusion(_fixture(tmp_path))

    summary = preparer.prepare_qualification_contexts(**inputs)

    assert summary["task_count"] == 10
    assert summary["image_count"] == 20


def test_swebench_grader_entrypoint_has_executable_interpreter_header() -> None:
    grader_entrypoint = SCRIPT.parents[1] / "sandbox" / "swebench" / "grader_entrypoint.py"

    assert grader_entrypoint.read_bytes().startswith(b"#!/usr/bin/env python3\n")


def test_refuses_prior_exclusion_file_that_disagrees_with_manifest(tmp_path: Path) -> None:
    inputs = _with_prior_qualification_exclusion(_fixture(tmp_path))
    exclusions = json.loads(inputs["prior_qualification_exclusions"].read_bytes())
    exclusions["instance_ids"][0] = "other__repo-00"
    exclusions_sha = _private_json(inputs["prior_qualification_exclusions"], exclusions)
    manifest = json.loads(inputs["qualification_manifest"].read_bytes())
    manifest["selection_policy"]["prior_qualification_exclusions_sha256"] = exclusions_sha
    inputs["qualification_manifest_sha256"] = _private_json(
        inputs["qualification_manifest"], manifest
    )

    with pytest.raises(preparer.PreparationError, match="do not match the prior manifest"):
        preparer.prepare_qualification_contexts(**inputs)

    assert inputs["control_runner"].commands == []
    assert not inputs["output_root"].exists()


def test_refuses_qualification_overlap_with_verified_500(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    manifest = json.loads(inputs["verified_manifest"].read_bytes())
    manifest["verified_500_instance_ids"][0] = "project__repo-00"
    manifest["tasks"][0]["instance_id"] = "project__repo-00"
    manifest["verified_500_ids_sha256"] = _sha256(
        json.dumps(manifest["verified_500_instance_ids"], separators=(",", ":")).encode()
    )
    inputs["verified_manifest_sha256"] = _private_json(inputs["verified_manifest"], manifest)

    with pytest.raises(preparer.PreparationError, match="disjoint"):
        preparer.prepare_qualification_contexts(**inputs)


def test_refuses_control_image_without_exact_repo_digest(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    runner = FakeControlRunner(inputs["control_image_reference"], repo_digests=[])
    inputs["control_runner"] = runner

    with pytest.raises(preparer.PreparationError, match="immutable digest"):
        preparer.prepare_qualification_contexts(**inputs)

    assert len(runner.commands) == 1
    assert not inputs["output_root"].exists()


def test_refuses_mismatched_control_prepare_identity_and_cleans_stage(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    runner = FakeControlRunner(inputs["control_image_reference"], corrupt_prepare_identity=True)
    inputs["control_runner"] = runner

    with pytest.raises(preparer.PreparationError, match="does not match frozen inputs"):
        preparer.prepare_qualification_contexts(**inputs)

    assert len(runner.commands) == 2
    assert not inputs["output_root"].exists()
    assert not list(inputs["output_root"].parent.glob(".prepared-stage-*"))


def test_execution_eval_script_accepts_official_five_greater_than_markers() -> None:
    assert preparer._execution_eval_script(_CANONICAL_EVAL_SCRIPT) == (
        _CANONICAL_EVAL_SCRIPT + b'exit "$SWEBENCH_TEST_EXIT_CODE"\n'
    )


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
    with pytest.raises(preparer.PreparationError, match="unambiguous test exit marker"):
        preparer._execution_eval_script(canonical_eval_script)


def test_refuses_generated_script_with_markers_out_of_order() -> None:
    canonical_eval_script = _CANONICAL_EVAL_SCRIPT.replace(
        b": '>>>>> End Test Output'\necho \">>>>> Test Exit Code: $SWEBENCH_TEST_EXIT_CODE\"\n",
        b"echo \">>>>> Test Exit Code: $SWEBENCH_TEST_EXIT_CODE\"\n: '>>>>> End Test Output'\n",
    )
    with pytest.raises(preparer.PreparationError, match="marker ordering is unexpected"):
        preparer._execution_eval_script(canonical_eval_script)


def test_refuses_generated_script_without_test_status_capture() -> None:
    with pytest.raises(preparer.PreparationError, match="unambiguous test exit marker"):
        preparer._execution_eval_script(b"#!/bin/bash\npytest -q\ngit checkout .\n")


def test_refuses_task_repository_or_record_drift(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    task_yaml = inputs["task_repository"] / "tasks" / "project__repo-00" / "task.yaml"
    metadata = yaml.safe_load(task_yaml.read_text(encoding="utf-8"))
    metadata["base_commit"] = "9" * 40
    task_yaml.write_text(yaml.safe_dump(metadata), encoding="utf-8")

    with pytest.raises(preparer.PreparationError, match="metadata"):
        preparer.prepare_qualification_contexts(**inputs)


def test_refuses_failed_dataset_check_or_wrong_task_repo_revision(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    evidence = json.loads(inputs["dataset_check_evidence"].read_bytes())
    evidence["status"] = "failed"
    inputs["dataset_check_evidence_sha256"] = _private_json(
        inputs["dataset_check_evidence"], evidence
    )

    with pytest.raises(preparer.PreparationError, match="dataset check"):
        preparer.prepare_qualification_contexts(**inputs)

    inputs = _fixture(tmp_path / "other")
    inputs["revision_reader"] = lambda _path: "0" * 40
    with pytest.raises(preparer.PreparationError, match="task repository revision"):
        preparer.prepare_qualification_contexts(**inputs)


def test_refuses_license_drift_unsafe_output_and_overwrite(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    inputs["license_authorization_receipt"].write_text("different\n", encoding="utf-8")
    with pytest.raises(preparer.PreparationError, match="license authorization"):
        preparer.prepare_qualification_contexts(**inputs)

    inputs = _fixture(tmp_path / "unsafe")
    inputs["output_root"] = inputs["repo_root"] / "private"
    with pytest.raises(preparer.PreparationError, match="outside the Git checkout"):
        preparer.prepare_qualification_contexts(**inputs)

    inputs = _fixture(tmp_path / "overwrite")
    preparer.prepare_qualification_contexts(**inputs)
    with pytest.raises(preparer.PreparationError, match="overwrite"):
        preparer.prepare_qualification_contexts(**inputs)


def test_refuses_base_image_or_grader_entrypoint_drift(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    images = json.loads(inputs["base_image_evidence"].read_bytes())
    images["images"][0]["dockerfile_sha256"] = "0" * 64
    inputs["base_image_evidence_sha256"] = _private_json(inputs["base_image_evidence"], images)
    with pytest.raises(preparer.PreparationError, match="Dockerfile"):
        preparer.prepare_qualification_contexts(**inputs)

    inputs = _fixture(tmp_path / "grader")
    inputs["grader_entrypoint"].chmod(0o600)
    inputs["grader_entrypoint"].write_text("drift\n", encoding="utf-8")
    with pytest.raises(preparer.PreparationError, match="grader entrypoint"):
        preparer.prepare_qualification_contexts(**inputs)


@pytest.mark.parametrize(
    ("provenance_change", "message"),
    [
        ("ubuntu", "base task image provenance"),
        ("missing", "base image fields do not match"),
    ],
    ids=["generic-ubuntu", "missing-image-reference"],
)
def test_refuses_base_image_without_frozen_task_image_provenance(
    tmp_path: Path, provenance_change: str, message: str
) -> None:
    inputs = _fixture(tmp_path)
    evidence = json.loads(inputs["base_image_evidence"].read_bytes())
    image = evidence["images"][0]
    if provenance_change == "ubuntu":
        image["image_reference"] = f"ubuntu@{image['image_digest']}"
    else:
        image.pop("image_reference")
    inputs["base_image_evidence_sha256"] = _private_json(inputs["base_image_evidence"], evidence)

    with pytest.raises(preparer.PreparationError, match=message):
        preparer.prepare_qualification_contexts(**inputs)

    assert inputs["control_runner"].commands == []


_VERIFIED_TASK_FIXTURE_COUNT = 3


def _verified_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Turn the shared fixture into a synthetic N-task frozen Verified selection."""
    count = _VERIFIED_TASK_FIXTURE_COUNT
    monkeypatch.setattr(preparer, "VERIFIED_TASK_COUNT", count)
    monkeypatch.setattr(builder, "VERIFIED_TASK_COUNT", count)
    inputs = _fixture(tmp_path)
    records = [_record(index) for index in range(count)]
    private = inputs["qualification_manifest"].parent
    records_path = private / "verified-records.jsonl"
    records_path.write_bytes(
        b"".join(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            for record in records
        )
    )
    records_path.chmod(0o600)
    qualification = json.loads(inputs["qualification_manifest"].read_bytes())
    tasks = qualification["tasks"][:count]
    ids = [task["instance_id"] for task in tasks]
    ids_sha = _sha256(json.dumps(ids, separators=(",", ":")).encode())
    inputs["verified_manifest_sha256"] = _private_json(
        inputs["verified_manifest"],
        {
            "schema_version": 1,
            "benchmark": "swebench_verified",
            "dataset_id": "SWE-bench/SWE-bench_Verified",
            "dataset_revision": VERIFIED_DATASET_REVISION,
            "split": "test",
            "task_count": count,
            "tasks": tasks,
            "records_path": "swebench/verified/records.jsonl",
            "records_sha256": _sha256(records_path.read_bytes()),
            "license_authorized": True,
            "license_authorization_revision": qualification["license_authorization_revision"],
            "frozen_before_tuning": True,
            "verified_500_instance_ids": ids,
            "verified_500_ids_sha256": ids_sha,
            "ordered_instance_ids_sha256": ids_sha,
            "source_sha256": "a" * 64,
            "selection_policy": {
                "schema_version": 1,
                "algorithm": "full_test_split_source_order",
                "candidate_dataset_id": "SWE-bench/SWE-bench_Verified",
                "candidate_split": "test",
                "output_order": "source_order",
                "evidence_class": "held_out_capability",
            },
        },
    )
    check = json.loads(inputs["dataset_check_evidence"].read_bytes())
    check["checked_instance_ids"] = ids
    inputs["dataset_check_evidence_sha256"] = _private_json(inputs["dataset_check_evidence"], check)
    base_images = json.loads(inputs["base_image_evidence"].read_bytes())
    base_images["images"] = base_images["images"][:count]
    inputs["base_image_evidence_sha256"] = _private_json(inputs["base_image_evidence"], base_images)
    for key in (
        "qualification_manifest",
        "qualification_manifest_sha256",
        "qualification_records",
    ):
        inputs.pop(key)
    inputs["verified_records"] = records_path
    inputs["scope"] = "verified"
    return inputs


def test_prepares_verified_scope_with_shared_recipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _verified_inputs(tmp_path, monkeypatch)
    count = _VERIFIED_TASK_FIXTURE_COUNT

    summary = preparer.prepare_qualification_contexts(**inputs)

    assert summary["scope"] == "verified"
    assert summary["task_count"] == count
    assert summary["image_count"] == count * 2
    spec_path = inputs["output_root"] / "build-spec.json"
    spec = json.loads(spec_path.read_bytes())
    assert spec["scope"] == "verified"
    assert spec["platform"] == "linux/amd64"
    assert spec["instance_ids"] == [f"project__repo-{index:02d}" for index in range(count)]
    assert spec["selection_manifest_path"] == str(inputs["verified_manifest"].resolve())
    assert spec["selection_manifest_sha256"] == inputs["verified_manifest_sha256"]
    provenance = json.loads((inputs["output_root"] / "provenance.json").read_bytes())
    assert provenance["scope"] == "verified"
    assert provenance["evidence_class"] == "held_out_capability"
    assert provenance["task_count"] == count
    assert provenance["image_count"] == count * 2
    assert "qualification_manifest_sha256" not in provenance
    assert len(list((inputs["output_root"] / "host-test-specs").glob("*.json"))) == count
    assert len(inputs["control_runner"].commands) == count + 1
    chown = "RUN chown -R 65532:65532 /testbed"
    for image in spec["images"]:
        assert image["tag"].endswith(f"-{image['role']}:verified")
        lines = (Path(image["context"]) / "Dockerfile").read_text(encoding="utf-8").splitlines()
        assert lines[0].startswith("FROM --platform=linux/amd64 ")
        assert lines[1] == chown
        assert lines.count(chown) == 1
    validated_spec, validated_sha = builder._validate_spec(
        inputs["repo_root"].resolve(), spec_path.resolve()
    )
    assert validated_spec["scope"] == "verified"
    assert validated_sha == summary["build_spec_sha256"]


def test_task_and_grader_recipes_share_the_first_layer_after_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _verified_inputs(tmp_path, monkeypatch)
    preparer.prepare_qualification_contexts(**inputs)
    spec = json.loads((inputs["output_root"] / "build-spec.json").read_bytes())
    by_binding = {
        (image["instance_id"], image["role"]): (Path(image["context"]) / "Dockerfile")
        .read_text(encoding="utf-8")
        .splitlines()
        for image in spec["images"]
    }

    for instance_id in spec["instance_ids"]:
        task = by_binding[(instance_id, "task")]
        grader = by_binding[(instance_id, "grader")]
        assert task[:2] == grader[:2]


@pytest.mark.parametrize(
    "change",
    ["full-count", "order", "qualification-input", "dataset-check"],
)
def test_verified_scope_refuses_drifted_or_mixed_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    inputs = _verified_inputs(tmp_path, monkeypatch)
    if change == "full-count":
        monkeypatch.setattr(preparer, "VERIFIED_TASK_COUNT", 500)
        message = "task count"
    elif change == "order":
        manifest = json.loads(inputs["verified_manifest"].read_bytes())
        manifest["verified_500_instance_ids"].reverse()
        inputs["verified_manifest_sha256"] = _private_json(inputs["verified_manifest"], manifest)
        message = "ordering or fingerprint"
    elif change == "qualification-input":
        inputs["qualification_records"] = inputs["verified_records"]
        message = "refuses qualification inputs"
    else:
        check = json.loads(inputs["dataset_check_evidence"].read_bytes())
        check["checked_instance_ids"].reverse()
        inputs["dataset_check_evidence_sha256"] = _private_json(
            inputs["dataset_check_evidence"], check
        )
        message = "dataset check"

    with pytest.raises(preparer.PreparationError, match=message):
        preparer.prepare_qualification_contexts(**inputs)

    assert inputs["control_runner"].commands == []
    assert not inputs["output_root"].exists()
