from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import local_evals.benchmarks as benchmarks
import local_evals.evalscope_exact as evalscope_exact
from local_evals.benchmarks import (
    CoreBenchmarkError,
    execute_evalscope_core,
    inspect_core_readiness,
)

FAMILY_COUNTS = {
    "gpqa_diamond": 12,
    "ifeval": 16,
    "mmlu_pro": 14,
    "tool_json": 10,
    "context": 8,
}

EVALSCOPE_DATASETS = {
    "gpqa_diamond": "gpqa_diamond",
    "ifeval": "ifeval",
    "mmlu_pro": "mmlu_pro",
    "tool_json": "racecraft_tool_json",
    "context": "racecraft_context",
}

MMLU_PRO_SUBSETS = [
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
]

FAMILY_RUNTIME = {
    "gpqa_diamond": ("train", ["default"]),
    "ifeval": ("train", ["default"]),
    "mmlu_pro": ("test", MMLU_PRO_SUBSETS),
    "tool_json": ("test", ["tool_json"]),
    "context": ("test", ["context"]),
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return _sha256(data)


def _tree_digest(root: Path) -> str:
    inventory = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        inventory.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256(path.read_bytes()),
            }
        )
    return _sha256(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode())


def _selection_digest(sample_ids: list[str]) -> str:
    return _sha256(json.dumps(sample_ids, separators=(",", ":")).encode())


def _record(family: str, sample_id: str, subset: str) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sample_id": sample_id,
        "subset": subset,
        "messages": [{"role": "user", "content": "synthetic fixture prompt"}],
    }
    if family == "tool_json":
        schema = {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        }
        return {
            **base,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "fixture_tool",
                        "description": "synthetic fixture",
                        "parameters": schema,
                    },
                }
            ],
            "expected_tool_call": {"name": "fixture_tool", "arguments": {"value": 1}},
        }
    if family == "context":
        return {**base, "targets": ["synthetic fixture answer"]}
    return {"sample_id": sample_id, "subset": subset, "private": "fixture"}


def _fixture(tmp_path: Path) -> tuple[dict[str, Any], Path, Path, Path]:
    repo = tmp_path / "repo"
    state = tmp_path / "private-state"
    repo.mkdir(parents=True)
    state.mkdir(mode=0o700, parents=True)
    executable = tmp_path / "evalscope"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)

    profile_families: dict[str, Any] = {}
    manifest_entries: dict[str, Any] = {}
    for family, count in FAMILY_COUNTS.items():
        dataset = state / "datasets" / family
        dataset.mkdir(parents=True)
        heldout_ids = [f"{family}-heldout-{index:02d}" for index in range(count)]
        calibration_ids = [f"{family}-calibration-00"]
        evalscope_split, evalscope_subsets = FAMILY_RUNTIME[family]
        ordered_subsets = (
            list(MMLU_PRO_SUBSETS) if family == "mmlu_pro" else [evalscope_subsets[0]] * count
        )
        records = "".join(
            json.dumps(_record(family, sample_id, subset)) + "\n"
            for sample_id, subset in zip(heldout_ids, ordered_subsets, strict=True)
        )
        records_name = f"{evalscope_split}.jsonl"
        records_path = dataset / records_name
        records_path.write_text(records, encoding="utf-8")
        dataset_index = {
            "schema_version": 1,
            "family": family,
            "evidence_split": "held_out",
            "evalscope_split": evalscope_split,
            "evalscope_subsets": evalscope_subsets,
            "record_count": count,
            "records_path": records_name,
            "records_sha256": _sha256(records.encode()),
            "sample_id_field": "sample_id",
            "subset_field": "subset",
            "ordered_sample_ids": heldout_ids,
            "ordered_subsets": ordered_subsets,
        }
        _write_json(dataset / "selection-index.json", dataset_index)
        profile_family = {
            "sample_count": count,
            "dataset_id": family,
            "evidence_split": "held_out",
            "evalscope_dataset": EVALSCOPE_DATASETS[family],
            "evalscope_split": evalscope_split,
            "evalscope_subsets": evalscope_subsets,
            "few_shot_num": 0,
            "few_shot_random": False,
            "prompt_template_revision": f"{family}-prompt-v1",
            "scorer_revision": (
                evalscope_exact.TOOL_SCORER_REVISION
                if family == "tool_json"
                else (
                    evalscope_exact.CONTEXT_SCORER_REVISION
                    if family == "context"
                    else f"{family}-scorer-v1"
                )
            ),
            "metric_variant": f"{family}-metric-v1",
        }
        if family in {"tool_json", "context"}:
            profile_family.update(
                {
                    "adapter_id": EVALSCOPE_DATASETS[family],
                    "adapter_revision": evalscope_exact.ADAPTER_REVISION,
                }
            )
        profile_families[family] = profile_family
        family_manifest = {
            "schema_version": 1,
            "family": family,
            **profile_family,
            "dataset_revision": "a" * 64,
            "dataset_path": (
                f"datasets/{family}/{records_name}"
                if family in {"tool_json", "context"}
                else f"datasets/{family}"
            ),
            "dataset_index_path": f"datasets/{family}/selection-index.json",
            "dataset_tree_sha256": _tree_digest(dataset),
            "ordered_sample_ids": heldout_ids,
            "ordered_sample_ids_sha256": _selection_digest(heldout_ids),
            "frozen_before_tuning": True,
            "contamination_review_revision": f"{family}-contamination-v1",
            "repeats": 1,
            "all_attempts_in_denominator": True,
            "license_authorized": True,
            "license_authorization_revision": f"{family}-license-v1",
            "calibration_ids": calibration_ids,
            "heldout_ids": heldout_ids,
        }
        if family in {"tool_json", "context"}:
            family_manifest.update(
                {
                    "adapter_id": EVALSCOPE_DATASETS[family],
                    "adapter_revision": evalscope_exact.ADAPTER_REVISION,
                    "adapter_source_sha256": _sha256(Path(evalscope_exact.__file__).read_bytes()),
                }
            )
        relative_manifest = f"manifests/{family}.json"
        family_hash = _write_json(state / relative_manifest, family_manifest)
        manifest_entries[family] = {
            "manifest_path": relative_manifest,
            "manifest_sha256": family_hash,
        }

    manifest_set = {"schema_version": 1, "families": manifest_entries}
    manifest_set_path = state / "manifests" / "core-manifest-set.json"
    manifest_set_hash = _write_json(manifest_set_path, manifest_set)
    profile = {
        "schema_version": 1,
        "suite": "core",
        "runner": "evalscope-1.12",
        "evalscope_version": "1.12.0",
        "model_alias": "racecraft-splash-local",
        "eval_type": "openai_api",
        "api_path": "/v1",
        "eval_batch_size": 1,
        "transport_retries": 0,
        "repetitions": 1,
        "max_output_tokens": 4096,
        "session_generated_token_cap": 250_000,
        "cache_policy": "disabled",
        "judge_policy": "disabled",
        "failure_policy": "all_attempts_in_denominator",
        "inspect_fallback": "unavailable",
        "manifest_set_path": "manifests/core-manifest-set.json",
        "manifest_set_sha256": manifest_set_hash,
        "families": profile_families,
    }
    return profile, repo, state, executable


def _inspect(profile: dict[str, Any], repo: Path, state: Path, executable: Path) -> dict[str, Any]:
    return inspect_core_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        evalscope_executable=str(executable),
        evalscope_version="1.12.0",
    )


def test_readiness_validates_frozen_core_with_exact_custom_adapters(tmp_path: Path) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    manifest_set = json.loads((state / profile["manifest_set_path"]).read_text(encoding="utf-8"))
    family_evidence = {}
    for family in FAMILY_COUNTS:
        manifest = _read_family_manifest(profile, state, family)
        family_evidence[family] = {
            "manifest_sha256": manifest_set["families"][family]["manifest_sha256"],
            "ordered_sample_ids_sha256": manifest["ordered_sample_ids_sha256"],
            "dataset_tree_sha256": manifest["dataset_tree_sha256"],
            "dataset_index_sha256": _sha256((state / manifest["dataset_index_path"]).read_bytes()),
            "evalscope_split": manifest["evalscope_split"],
            "evalscope_subsets": manifest["evalscope_subsets"],
            "few_shot_num": manifest["few_shot_num"],
            "few_shot_random": manifest["few_shot_random"],
        }
        if family in {"tool_json", "context"}:
            family_evidence[family].update(
                {
                    "adapter_id": manifest["adapter_id"],
                    "adapter_revision": manifest["adapter_revision"],
                    "adapter_source_sha256": manifest["adapter_source_sha256"],
                }
            )

    result = _inspect(profile, repo, state, executable)

    assert result == {
        "status": "ready",
        "blockers": [],
        "metadata": {
            "suite": "core",
            "runner": "evalscope-1.12",
            "evalscope_version": "1.12.0",
            "model_alias": "racecraft-splash-local",
            "eval_type": "openai_api",
            "max_output_tokens": 4096,
            "session_generated_token_cap": 250_000,
            "families": FAMILY_COUNTS,
            "total_samples": 60,
            "coding_requested": False,
            "manifest_set_sha256": profile["manifest_set_sha256"],
            "family_evidence": family_evidence,
        },
    }
    serialized = json.dumps(result)
    assert str(repo) not in serialized
    assert str(state) not in serialized
    assert "gpqa_diamond-heldout-00" not in serialized


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("evalscope_version", "1.11.1", "EvalScope version"),
        ("model_alias", "other", "model alias"),
        ("eval_type", "openai_responses_api", "evaluation type"),
        ("eval_batch_size", 2, "batch size"),
        ("transport_retries", 1, "transport retries"),
        ("repetitions", 2, "repetitions"),
        ("failure_policy", "successful_only", "denominator"),
    ],
)
def test_readiness_rejects_nonexact_runtime_contract(
    tmp_path: Path, field: str, value: Any, message: str
) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    profile[field] = value

    result = _inspect(profile, repo, state, executable)

    assert result["status"] == "blocked"
    assert message in result["blockers"][0]


def test_readiness_rejects_non_loopback_or_non_v1_server(tmp_path: Path) -> None:
    profile, repo, state, executable = _fixture(tmp_path)

    result = inspect_core_readiness(
        profile,
        repo=repo,
        state=state,
        server_origin="https://api.example.com/v1",
        evalscope_executable=str(executable),
        evalscope_version="1.12.0",
    )

    assert result["status"] == "blocked"
    assert "loopback" in result["blockers"][0]


def test_readiness_rejects_manifest_set_or_family_hash_drift(tmp_path: Path) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    profile["manifest_set_sha256"] = "0" * 64
    assert "manifest-set hash" in _inspect(profile, repo, state, executable)["blockers"][0]

    profile, repo, state, executable = _fixture(tmp_path / "second")
    manifest_set_path = state / str(profile["manifest_set_path"])
    manifest_set = json.loads(manifest_set_path.read_text(encoding="utf-8"))
    manifest_set["families"]["gpqa_diamond"]["manifest_sha256"] = "0" * 64
    profile["manifest_set_sha256"] = _write_json(manifest_set_path, manifest_set)
    assert "gpqa_diamond manifest hash" in _inspect(profile, repo, state, executable)["blockers"][0]


def test_readiness_rejects_missing_manifest_and_paths_inside_git(tmp_path: Path) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    manifest_set = json.loads(
        (state / str(profile["manifest_set_path"])).read_text(encoding="utf-8")
    )
    missing_path = state / manifest_set["families"]["context"]["manifest_path"]
    missing_path.unlink()
    assert "unsafe or unavailable" in _inspect(profile, repo, state, executable)["blockers"][0]

    profile, repo, state, executable = _fixture(tmp_path / "git-path")
    profile["manifest_set_path"] = str(repo / "private-manifest-set.json")
    assert "external-state-relative" in _inspect(profile, repo, state, executable)["blockers"][0]


@pytest.mark.parametrize("field", ["scorer_revision", "metric_variant"])
def test_readiness_rejects_scorer_or_metric_mismatch(tmp_path: Path, field: str) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    _mutate_family_manifest(profile, state, "tool_json", field, "mismatched-v2")

    result = _inspect(profile, repo, state, executable)

    assert result["status"] == "blocked"
    assert field in result["blockers"][0]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("adapter_id", "unqualified-adapter", "adapter ID"),
        ("adapter_revision", "mutable-main", "adapter revision"),
    ],
)
def test_readiness_rejects_custom_adapter_profile_drift(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    profile["families"]["tool_json"][field] = value

    result = _inspect(profile, repo, state, executable)

    assert result["status"] == "blocked"
    assert message in result["blockers"][0]


def test_readiness_rejects_custom_adapter_source_or_private_record_drift(tmp_path: Path) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    _mutate_family_manifest(profile, state, "context", "adapter_source_sha256", "0" * 64)
    assert "adapter source digest" in _inspect(profile, repo, state, executable)["blockers"][0]

    profile, repo, state, executable = _fixture(tmp_path / "record")
    records = _read_dataset_records(profile, state, "tool_json")
    records[0]["expected_tool_call"]["arguments"] = {"unexpected": 1}
    _replace_dataset_records(profile, state, "tool_json", records)
    assert (
        "private scorer record is invalid"
        in _inspect(profile, repo, state, executable)["blockers"][0]
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("dataset_revision", "mutable-main", "dataset revision"),
        ("frozen_before_tuning", False, "frozen before tuning"),
        ("contamination_review_revision", "", "contamination"),
        ("repeats", 2, "repeats"),
        ("all_attempts_in_denominator", False, "denominator"),
        ("license_authorized", False, "license"),
    ],
)
def test_readiness_rejects_family_protocol_gaps(
    tmp_path: Path, field: str, value: Any, message: str
) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    _mutate_family_manifest(profile, state, "gpqa_diamond", field, value)

    result = _inspect(profile, repo, state, executable)

    assert result["status"] == "blocked"
    assert message in result["blockers"][0]


def test_readiness_rejects_sample_hash_count_duplicates_and_overlap(tmp_path: Path) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    _mutate_family_manifest(profile, state, "ifeval", "ordered_sample_ids_sha256", "0" * 64)
    assert "ordered sample hash" in _inspect(profile, repo, state, executable)["blockers"][0]

    profile, repo, state, executable = _fixture(tmp_path / "duplicate")
    family = _read_family_manifest(profile, state, "ifeval")
    sample_ids = list(family["ordered_sample_ids"])
    sample_ids[-1] = sample_ids[0]
    family["ordered_sample_ids"] = sample_ids
    family["ordered_sample_ids_sha256"] = _selection_digest(sample_ids)
    family["heldout_ids"] = sample_ids
    _replace_family_manifest(profile, state, "ifeval", family)
    assert "unique" in _inspect(profile, repo, state, executable)["blockers"][0]

    profile, repo, state, executable = _fixture(tmp_path / "overlap")
    family = _read_family_manifest(profile, state, "ifeval")
    family["calibration_ids"] = [family["heldout_ids"][0]]
    _replace_family_manifest(profile, state, "ifeval", family)
    assert "disjoint" in _inspect(profile, repo, state, executable)["blockers"][0]


def test_readiness_rejects_session_budget_and_evalscope_split_or_subset_drift(
    tmp_path: Path,
) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    profile["max_output_tokens"] = 4167
    assert "250000-token session cap" in _inspect(profile, repo, state, executable)["blockers"][0]

    profile, repo, state, executable = _fixture(tmp_path / "split")
    profile["families"]["mmlu_pro"]["evalscope_split"] = "validation"
    assert "adapter contract" in _inspect(profile, repo, state, executable)["blockers"][0]

    profile, repo, state, executable = _fixture(tmp_path / "subsets")
    profile["families"]["mmlu_pro"]["evalscope_subsets"] = MMLU_PRO_SUBSETS[:-1]
    assert "adapter contract" in _inspect(profile, repo, state, executable)["blockers"][0]


def test_readiness_rejects_index_count_and_actual_id_or_subset_drift(tmp_path: Path) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    index = _read_dataset_index(profile, state, "gpqa_diamond")
    index["record_count"] = 11
    _replace_dataset_index(profile, state, "gpqa_diamond", index)
    assert "record count must be 12" in _inspect(profile, repo, state, executable)["blockers"][0]

    profile, repo, state, executable = _fixture(tmp_path / "ids")
    records = _read_dataset_records(profile, state, "ifeval")
    records[0]["sample_id"] = "drifted-private-id"
    _replace_dataset_records(profile, state, "ifeval", records)
    assert "actual record IDs" in _inspect(profile, repo, state, executable)["blockers"][0]

    profile, repo, state, executable = _fixture(tmp_path / "subsets")
    records = _read_dataset_records(profile, state, "mmlu_pro")
    records[-1]["subset"] = "computer science"
    index = _read_dataset_index(profile, state, "mmlu_pro")
    index["ordered_subsets"][-1] = "computer science"
    _replace_dataset_records(profile, state, "mmlu_pro", records, index=index)
    assert "EvalScope subsets" in _inspect(profile, repo, state, executable)["blockers"][0]


def test_readiness_rejects_dataset_tree_drift_and_symlinks(tmp_path: Path) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    (state / "datasets" / "context" / "test.jsonl").write_text("drift", encoding="utf-8")
    assert "dataset tree digest" in _inspect(profile, repo, state, executable)["blockers"][0]

    profile, repo, state, executable = _fixture(tmp_path / "symlink")
    target = state / "datasets" / "context" / "test.jsonl"
    link = state / "datasets" / "context" / "linked.jsonl"
    link.symlink_to(target)
    family = _read_family_manifest(profile, state, "context")
    family["dataset_tree_sha256"] = _tree_digest(state / "datasets" / "context")
    _replace_family_manifest(profile, state, "context", family)
    assert "symbolic links" in _inspect(profile, repo, state, executable)["blockers"][0]


def test_readiness_rejects_extra_records_or_records_file_for_wrong_split(tmp_path: Path) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    dataset = state / "datasets" / "gpqa_diamond"
    (dataset / "extra.jsonl").write_text('{"sample_id":"extra"}\n', encoding="utf-8")
    manifest = _read_family_manifest(profile, state, "gpqa_diamond")
    manifest["dataset_tree_sha256"] = _tree_digest(dataset)
    _replace_family_manifest(profile, state, "gpqa_diamond", manifest)
    assert "only the indexed records" in _inspect(profile, repo, state, executable)["blockers"][0]

    profile, repo, state, executable = _fixture(tmp_path / "wrong-split-file")
    dataset = state / "datasets" / "gpqa_diamond"
    (dataset / "heldout.jsonl").write_bytes((dataset / "train.jsonl").read_bytes())
    (dataset / "train.jsonl").unlink()
    index = _read_dataset_index(profile, state, "gpqa_diamond")
    index["records_path"] = "heldout.jsonl"
    _replace_dataset_index(profile, state, "gpqa_diamond", index)
    assert (
        "does not match the EvalScope split"
        in _inspect(profile, repo, state, executable)["blockers"][0]
    )


def test_coding_request_requires_valid_sandbox_attestation_and_caps_samples(
    tmp_path: Path,
) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    profile["coding"] = {"requested": True, "sample_count": 10}
    assert "sandbox policy" in _inspect(profile, repo, state, executable)["blockers"][0]

    profile["coding"]["sample_count"] = 11
    assert "at most 10" in _inspect(profile, repo, state, executable)["blockers"][0]


def test_coding_uses_shared_attestation_contract_but_remains_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    image_digest = "sha256:" + "b" * 64
    policy = {"image_reference": image_digest, "image_digest": image_digest}
    policy_path = "sandbox/policy.json"
    policy_hash = _write_json(state / policy_path, policy)
    attestation = {
        "schema_version": 1,
        "qualified": True,
        "disposable": True,
        "network_disabled": True,
        "host_home_mounted": False,
        "docker_socket_mounted": False,
        "attestation_revision": "sandbox-v1",
    }
    attestation_path = "sandbox/attestation.json"
    attestation_hash = _write_json(state / attestation_path, attestation)
    validated = False

    def shared_validator(evidence: Mapping[str, Any], policy_value: object) -> object:
        nonlocal validated
        validated = True
        assert evidence == attestation
        assert isinstance(policy_value, benchmarks.SandboxPolicy)
        assert policy_value.image_digest == image_digest
        return object()

    monkeypatch.setattr(benchmarks, "validate_attestation", shared_validator)
    profile["coding"] = {
        "requested": True,
        "sample_count": 10,
        "sandbox_policy_path": policy_path,
        "sandbox_policy_sha256": policy_hash,
        "sandbox_attestation_path": attestation_path,
        "sandbox_attestation_sha256": attestation_hash,
    }

    result = _inspect(profile, repo, state, executable)

    assert result["status"] == "blocked"
    assert "coding execution is unavailable" in result["blockers"][0]
    assert validated is True


def test_all_commands_pin_exact_selection_generation_and_adapter_contract(tmp_path: Path) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    prepared = benchmarks._prepare(
        profile,
        repo=repo,
        state=state,
        server_origin="http://localhost:1234/v1/",
        evalscope_executable=str(executable),
        evalscope_version="1.12.0",
    )
    work_root = state / "command-contract"
    work_root.mkdir()
    calls = [
        benchmarks._family_command(prepared, family, work_root / family.name)
        for family in prepared.families
    ]
    assert [command[command.index("--datasets") + 1] for command in calls] == [
        "gpqa_diamond",
        "ifeval",
        "mmlu_pro",
        "racecraft_tool_json",
        "racecraft_context",
    ]
    for family, command in zip(prepared.families, calls, strict=True):
        if family.name in {"tool_json", "context"}:
            assert command[1:4] == ["-m", "local_evals.evalscope_exact", "eval"]
        else:
            assert command[:2] == [str(executable), "eval"]
        assert command[command.index("--model") + 1] == "racecraft-splash-local"
        assert command[command.index("--eval-type") + 1] == "openai_api"
        assert command[command.index("--api-url") + 1] == "http://localhost:1234/v1"
        assert command[command.index("--eval-batch-size") + 1] == "1"
        assert command[command.index("--repeats") + 1] == "1"
        assert "--limit" not in command
        assert json.loads(command[command.index("--generation-config") + 1]) == {
            "max_tokens": 4096,
            "retries": 0,
            "stream": False,
        }
        assert "--judge" not in command
        assert "--use-cache" not in command
        assert Path(command[command.index("--work-dir") + 1]).is_relative_to(state)
        assert Path(command[command.index("--dataset-dir") + 1]).is_relative_to(state)
        dataset_args = json.loads(command[command.index("--dataset-args") + 1])
        only_args = next(iter(dataset_args.values()))
        assert Path(only_args["dataset_id"]).is_relative_to(state)
        assert only_args["shuffle"] is False
        assert only_args["subset_list"] == FAMILY_RUNTIME[family.name][1]
        assert only_args["few_shot_num"] == 0
        assert only_args["few_shot_random"] is False
        assert "extra_params" not in only_args

    mmlu_command = calls[2]
    mmlu_args = json.loads(mmlu_command[mmlu_command.index("--dataset-args") + 1])["mmlu_pro"]
    assert mmlu_args["subset_list"] == MMLU_PRO_SUBSETS
    assert len(mmlu_args["subset_list"]) == 14


def test_mock_executable_accepts_each_builtin_family_command(tmp_path: Path) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    prepared = benchmarks._prepare(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        evalscope_executable=str(executable),
        evalscope_version="1.12.0",
    )
    for family in prepared.families[:3]:
        command = benchmarks._family_command(prepared, family, state / "work" / family.name)
        completed = subprocess.run(  # noqa: S603 - absolute fixture executable, no shell
            command, capture_output=True, text=True, check=False
        )
        assert completed.returncode == 0


def test_core_execution_invokes_all_five_qualified_families(tmp_path: Path) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    calls: list[list[str]] = []

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(args[0]))
        return subprocess.CompletedProcess(args, 0, "", "")

    result = execute_evalscope_core(
        profile,
        repo=repo,
        state=state,
        server_origin="http://127.0.0.1:1234/v1",
        evalscope_executable=str(executable),
        evalscope_version="1.12.0",
        runner=runner,
    )

    assert result == {
        "status": "completed",
        "families_completed": list(FAMILY_COUNTS),
        "family_count": 5,
        "sample_count": 60,
    }
    assert [call[call.index("--datasets") + 1] for call in calls] == list(
        EVALSCOPE_DATASETS.values()
    )


def test_execute_raises_value_error_subclass_before_runner_on_invalid_input(
    tmp_path: Path,
) -> None:
    profile, repo, state, executable = _fixture(tmp_path)
    profile["families"]["context"]["sample_count"] = 9
    called = False

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess(args, 0, "", "")

    with pytest.raises(CoreBenchmarkError, match="context sample count"):
        execute_evalscope_core(
            profile,
            repo=repo,
            state=state,
            server_origin="http://127.0.0.1:1234/v1",
            evalscope_executable=str(executable),
            evalscope_version="1.12.0",
            runner=runner,
        )

    assert called is False


def _read_family_manifest(profile: dict[str, Any], state: Path, family: str) -> dict[str, Any]:
    manifest_set = json.loads((state / profile["manifest_set_path"]).read_text(encoding="utf-8"))
    path = state / manifest_set["families"][family]["manifest_path"]
    return json.loads(path.read_text(encoding="utf-8"))


def _replace_family_manifest(
    profile: dict[str, Any], state: Path, family: str, manifest: Mapping[str, Any]
) -> None:
    manifest_set_path = state / profile["manifest_set_path"]
    manifest_set = json.loads(manifest_set_path.read_text(encoding="utf-8"))
    entry = manifest_set["families"][family]
    entry["manifest_sha256"] = _write_json(state / entry["manifest_path"], manifest)
    profile["manifest_set_sha256"] = _write_json(manifest_set_path, manifest_set)


def _mutate_family_manifest(
    profile: dict[str, Any], state: Path, family: str, field: str, value: Any
) -> None:
    manifest = _read_family_manifest(profile, state, family)
    manifest[field] = value
    _replace_family_manifest(profile, state, family, manifest)


def _read_dataset_index(profile: dict[str, Any], state: Path, family: str) -> dict[str, Any]:
    manifest = _read_family_manifest(profile, state, family)
    return json.loads((state / manifest["dataset_index_path"]).read_text(encoding="utf-8"))


def _read_dataset_records(
    profile: dict[str, Any], state: Path, family: str
) -> list[dict[str, Any]]:
    manifest = _read_family_manifest(profile, state, family)
    index = _read_dataset_index(profile, state, family)
    dataset_path = state / manifest["dataset_path"]
    root = dataset_path if dataset_path.is_dir() else dataset_path.parent
    return [
        json.loads(line)
        for line in (root / index["records_path"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _replace_dataset_index(
    profile: dict[str, Any], state: Path, family: str, index: Mapping[str, Any]
) -> None:
    manifest = _read_family_manifest(profile, state, family)
    _write_json(state / manifest["dataset_index_path"], index)
    dataset_path = state / manifest["dataset_path"]
    root = dataset_path if dataset_path.is_dir() else dataset_path.parent
    manifest["dataset_tree_sha256"] = _tree_digest(root)
    _replace_family_manifest(profile, state, family, manifest)


def _replace_dataset_records(
    profile: dict[str, Any],
    state: Path,
    family: str,
    records: list[dict[str, Any]],
    *,
    index: dict[str, Any] | None = None,
) -> None:
    manifest = _read_family_manifest(profile, state, family)
    updated_index = index or _read_dataset_index(profile, state, family)
    dataset_path = state / manifest["dataset_path"]
    root = dataset_path if dataset_path.is_dir() else dataset_path.parent
    encoded = "".join(json.dumps(record) + "\n" for record in records).encode()
    (root / updated_index["records_path"]).write_bytes(encoded)
    updated_index["records_sha256"] = _sha256(encoded)
    _replace_dataset_index(profile, state, family, updated_index)
