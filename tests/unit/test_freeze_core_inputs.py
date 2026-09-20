from __future__ import annotations

import importlib
import json
import stat
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest

from local_evals import benchmarks, evalscope_exact

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import freeze_core_inputs as freezer  # noqa: E402

pa: Any = importlib.import_module("py" + "arrow")
ipc: Any = importlib.import_module("py" + "arrow.ipc")


def _write_arrow(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    with ipc.new_file(path, table.schema) as writer:
        writer.write_table(table)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _tool_record(index: int) -> dict[str, Any]:
    return {
        "sample_id": f"tool-{index:02d}",
        "subset": "tool_json",
        "messages": [{"role": "user", "content": f"PRIVATE_TOOL_PROMPT_{index}"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "private",
                    "parameters": {
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
        "expected_tool_call": {"name": "lookup", "arguments": {"value": index}},
    }


def _context_record(index: int) -> dict[str, Any]:
    return {
        "sample_id": f"context-{index:02d}",
        "subset": "context",
        "messages": [{"role": "user", "content": f"PRIVATE_CONTEXT_PROMPT_{index}"}],
        "targets": [f"private target {index}"],
    }


def _sources(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "sources"
    gpqa = root / "gpqa" / "train.arrow"
    ifeval = root / "ifeval" / "train.arrow"
    mmlu = root / "mmlu" / "test.arrow"
    tool = root / "tool.jsonl"
    context = root / "context.jsonl"
    _write_arrow(
        gpqa,
        [
            {
                "Record ID": f"gpqa-{index:02d}",
                "Question": f"PRIVATE_GPQA_PROMPT_{index}",
                "Correct Answer": "answer",
            }
            for index in range(13)
        ],
    )
    _write_arrow(
        ifeval,
        [
            {
                "key": index,
                "prompt": f"PRIVATE_IFEVAL_PROMPT_{index}",
                "instruction_id_list": ["keywords:existence"],
                "kwargs": [{"keywords": ["x"]}],
            }
            for index in range(17)
        ],
    )
    mmlu_rows = [
        {
            "question_id": f"mmlu-{index:02d}",
            "category": subset,
            "question": f"PRIVATE_MMLU_PROMPT_{index}",
            "options": ["a", "b", "c", "d"],
            "answer": "A",
        }
        for index, subset in enumerate(benchmarks.MMLU_PRO_SUBSETS)
    ]
    mmlu_rows.append(
        {
            "question_id": "mmlu-calibration",
            "category": benchmarks.MMLU_PRO_SUBSETS[0],
            "question": "PRIVATE_MMLU_CALIBRATION",
            "options": ["a", "b", "c", "d"],
            "answer": "A",
        }
    )
    _write_arrow(mmlu, mmlu_rows)
    _write_jsonl(tool, [_tool_record(index) for index in range(11)])
    _write_jsonl(context, [_context_record(index) for index in range(9)])
    return {
        "gpqa_diamond": gpqa,
        "ifeval": ifeval,
        "mmlu_pro": mmlu,
        "tool_json": tool,
        "context": context,
    }


def _args(tmp_path: Path, sources: dict[str, Path], *, output: str = "state") -> Namespace:
    state = tmp_path / output
    state.mkdir(mode=0o700)
    authorizations = [f"{family}={family}-license-v1" for family in freezer._FAMILY_ORDER]
    reviews = [f"{family}={family}-review-v1" for family in freezer._FAMILY_ORDER]
    return Namespace(
        repo_root=str(freezer._REPO_ROOT),
        output_root=str(state),
        gpqa_source=str(sources["gpqa_diamond"]),
        ifeval_source=str(sources["ifeval"]),
        mmlu_pro_source=str(sources["mmlu_pro"]),
        tool_json_source=str(sources["tool_json"]),
        context_source=str(sources["context"]),
        authorize_license=authorizations,
        contamination_review=reviews,
    )


def _profile(state: Path, manifest_hash: str) -> dict[str, Any]:
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
        "manifest_set_sha256": manifest_hash,
        "families": {family: freezer._profile_fields(family) for family in freezer._FAMILY_ORDER},
    }


def _artifact_bytes(state: Path) -> dict[str, bytes]:
    root = state / "benchmarks" / "core"
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_freeze_is_deterministic_disjoint_private_and_validator_compatible(
    tmp_path: Path,
) -> None:
    sources = _sources(tmp_path)
    first = freezer.freeze(_args(tmp_path, sources, output="state-one"))
    second = freezer.freeze(_args(tmp_path, sources, output="state-two"))

    assert first == second
    assert first["families"] == {
        "gpqa_diamond": 12,
        "ifeval": 16,
        "mmlu_pro": 14,
        "tool_json": 10,
        "context": 8,
    }
    first_state = tmp_path / "state-one"
    second_state = tmp_path / "state-two"
    assert _artifact_bytes(first_state) == _artifact_bytes(second_state)

    core = first_state / "benchmarks" / "core"
    for path in [first_state, first_state / "benchmarks", core, *core.rglob("*")]:
        expected = 0o700 if path.is_dir() else 0o600
        assert stat.S_IMODE(path.stat().st_mode) == expected
    for family in freezer._FAMILY_ORDER:
        manifest = json.loads((core / "manifests" / f"{family}.json").read_bytes())
        assert set(manifest["calibration_ids"]).isdisjoint(manifest["heldout_ids"])
        assert manifest["ordered_sample_ids"] == manifest["heldout_ids"]
        assert len(manifest["heldout_ids"]) == freezer._CONTRACTS[family].count
        assert len(manifest["dataset_revision"]) == 64

    executable = tmp_path / "evalscope"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    readiness = benchmarks.inspect_core_readiness(
        _profile(first_state, str(first["manifest_set_sha256"])),
        repo=freezer._REPO_ROOT,
        state=first_state,
        server_origin="http://127.0.0.1:1234/v1",
        evalscope_executable=str(executable),
        evalscope_version="1.12.0",
    )
    assert readiness["status"] == "ready", readiness


def test_main_stdout_is_sanitized(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sources = _sources(tmp_path)
    args = _args(tmp_path, sources)
    argv = [
        "--repo-root",
        args.repo_root,
        "--output-root",
        args.output_root,
        "--gpqa-source",
        args.gpqa_source,
        "--ifeval-source",
        args.ifeval_source,
        "--mmlu-pro-source",
        args.mmlu_pro_source,
        "--tool-json-source",
        args.tool_json_source,
        "--context-source",
        args.context_source,
    ]
    for value in args.authorize_license:
        argv.extend(["--authorize-license", value])
    for value in args.contamination_review:
        argv.extend(["--contamination-review", value])

    assert freezer.main(argv) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "PRIVATE_" not in captured.out
    assert str(tmp_path) not in captured.out
    assert "gpqa-" not in captured.out
    payload = json.loads(captured.out)
    assert payload["status"] == "frozen"
    assert set(payload) == {"families", "manifest_set_sha256", "status"}


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_gpqa", "all five local sources"),
        ("missing_license", "license authorization"),
        ("overwrite", "overwrite"),
        ("duplicate_ids", "duplicate sample IDs"),
        ("insufficient_rows", "insufficient rows"),
        ("category_mismatch", "category coverage"),
        ("schema_drift", "schema drift"),
        ("invalid_custom", "exact-scorer record"),
    ],
)
def test_freeze_refuses_invalid_or_ambiguous_inputs(
    tmp_path: Path, mutation: str, message: str
) -> None:
    sources = _sources(tmp_path)
    args = _args(tmp_path, sources)
    if mutation == "missing_gpqa":
        args.gpqa_source = None
    elif mutation == "missing_license":
        args.authorize_license = args.authorize_license[:-1]
    elif mutation == "overwrite":
        (Path(args.output_root) / "benchmarks" / "core").mkdir(parents=True)
    elif mutation == "duplicate_ids":
        rows = [_context_record(index) for index in range(9)]
        rows[-1]["sample_id"] = rows[0]["sample_id"]
        _write_jsonl(sources["context"], rows)
    elif mutation == "insufficient_rows":
        _write_jsonl(sources["context"], [_context_record(index) for index in range(8)])
    elif mutation == "category_mismatch":
        _write_arrow(
            sources["mmlu_pro"],
            [
                {
                    "question_id": f"bad-{index}",
                    "category": "not-a-category",
                    "question": "private",
                }
                for index in range(15)
            ],
        )
    elif mutation == "schema_drift":
        other = sources["ifeval"].parent / "other.arrow"
        _write_arrow(other, [{"key": "other", "unexpected": "private"}])
        args.ifeval_source = str(sources["ifeval"].parent)
    elif mutation == "invalid_custom":
        rows = [_tool_record(index) for index in range(11)]
        rows[-1]["tools"] = []
        _write_jsonl(sources["tool_json"], rows)

    with pytest.raises(freezer.FreezeError, match=message):
        freezer.freeze(args)
    if mutation != "overwrite":
        assert not (Path(args.output_root) / "benchmarks" / "core").exists()


def test_freeze_refuses_symlinks_and_git_sources(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    args = _args(tmp_path, sources, output="state-symlink")
    link = tmp_path / "context-link.jsonl"
    link.symlink_to(sources["context"])
    args.context_source = str(link)
    with pytest.raises(freezer.FreezeError, match="symbolic-link"):
        freezer.freeze(args)

    sources = _sources(tmp_path / "second")
    args = _args(tmp_path / "second", sources, output="state-git")
    git_source = tmp_path / "second" / "git-source"
    (git_source / ".git").mkdir(parents=True)
    private_source = git_source / "context.jsonl"
    _write_jsonl(private_source, [_context_record(index) for index in range(9)])
    args.context_source = str(private_source)
    with pytest.raises(freezer.FreezeError, match="outside Git"):
        freezer.freeze(args)


def test_freeze_refuses_output_parent_escape_and_git_output(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    args = _args(tmp_path, sources, output="state-parent-link")
    escaped = tmp_path / "escaped"
    escaped.mkdir()
    (Path(args.output_root) / "benchmarks").symlink_to(escaped, target_is_directory=True)
    with pytest.raises(freezer.FreezeError, match="unsafe entry"):
        freezer.freeze(args)
    assert not any(escaped.iterdir())

    git_state = tmp_path / "git-state"
    (git_state / ".git").mkdir(parents=True)
    args = _args(tmp_path, sources, output="ordinary-state")
    args.output_root = str(git_state)
    with pytest.raises(freezer.FreezeError, match="outside Git"):
        freezer.freeze(args)


def test_adapter_provenance_uses_current_exact_adapter(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    result = freezer.freeze(_args(tmp_path, sources))
    core = tmp_path / "state" / "benchmarks" / "core"
    expected_source_hash = freezer._sha256(Path(evalscope_exact.__file__).read_bytes())
    for family in ("tool_json", "context"):
        manifest = json.loads((core / "manifests" / f"{family}.json").read_bytes())
        assert manifest["adapter_id"] == freezer._CONTRACTS[family].evalscope_dataset
        assert manifest["adapter_revision"] == evalscope_exact.ADAPTER_REVISION
        assert manifest["adapter_source_sha256"] == expected_source_hash
    assert len(str(result["manifest_set_sha256"])) == 64
