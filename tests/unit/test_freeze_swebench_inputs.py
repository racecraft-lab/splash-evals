from __future__ import annotations

import hashlib
import json
import stat
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import freeze_swebench_inputs as freezer  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(instance_id: str, index: int) -> dict[str, Any]:
    return {
        "instance_id": instance_id,
        "repo": "example/project",
        "base_commit": f"{index + 1:040x}",
        "problem_statement": f"PRIVATE_PROMPT_{index}",
        "patch": f"PRIVATE_PATCH_{index}",
        "test_patch": f"PRIVATE_TEST_PATCH_{index}",
        "version": "1.0",
        "FAIL_TO_PASS": [f"test_fail_{index}"],
        "PASS_TO_PASS": [f"test_pass_{index}"],
        "environment_setup_commit": f"{index + 2:040x}",
        "extra_official_field": {"index": index},
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_exclusions(path: Path, instance_ids: list[str], manifest_sha256: str = "9" * 64) -> None:
    path.write_bytes(
        json.dumps(
            {
                "schema_version": 1,
                "prior_manifest_sha256": manifest_sha256,
                "instance_ids": instance_ids,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    verified = tmp_path / "inputs" / "verified.jsonl"
    parent = tmp_path / "inputs" / "parent.jsonl"
    receipt = tmp_path / "inputs" / "license-receipt.txt"
    exclusions = tmp_path / "inputs" / "prior-qualification-exclusions.json"
    _write_jsonl(verified, [_record(f"verified-{index:03d}", index) for index in range(500)])
    _write_jsonl(
        parent,
        [_record(f"verified-{index:03d}", index) for index in range(500)]
        + [_record(f"qualification-{index:03d}", index + 500) for index in range(20)],
    )
    receipt.write_text("authorized for private local evaluation\n", encoding="utf-8")
    _write_exclusions(exclusions, [f"qualification-{index:03d}" for index in range(10)])
    return verified, parent, receipt, exclusions


def _args(tmp_path: Path, *, output: str = "state", count: int = 5) -> Namespace:
    verified, parent, receipt, exclusions = _inputs(tmp_path)
    state = tmp_path / output
    state.mkdir(mode=0o700)
    return Namespace(
        repo_root=str(freezer._REPO_ROOT),
        output_root=str(state),
        verified_source=str(verified),
        verified_source_sha256=_sha256(verified),
        verified_dataset_id="princeton-nlp/SWE-bench_Verified",
        verified_dataset_revision="1" * 40,
        parent_source=str(parent),
        parent_source_sha256=_sha256(parent),
        parent_dataset_id="princeton-nlp/SWE-bench",
        parent_dataset_revision="2" * 40,
        prior_qualification_exclusions=str(exclusions),
        prior_qualification_exclusions_sha256=_sha256(exclusions),
        license_authorization_receipt=str(receipt),
        license_authorization_revision="license-review-2026-09-21",
        qualification_count=count,
    )


def _artifacts(state: Path) -> dict[str, bytes]:
    root = state / "swebench"
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_freeze_is_deterministic_disjoint_canonical_and_private(tmp_path: Path) -> None:
    first_args = _args(tmp_path / "one", output="state-one")
    second_args = _args(tmp_path / "two", output="state-two")
    first = freezer.freeze(first_args)
    second = freezer.freeze(second_args)

    assert first == second
    assert first["counts"] == {"qualification": 5, "verified": 500}
    first_state = tmp_path / "one" / "state-one"
    second_state = tmp_path / "two" / "state-two"
    assert _artifacts(first_state) == _artifacts(second_state)

    root = first_state / "swebench"
    verified_manifest = json.loads((root / "verified" / "manifest.json").read_bytes())
    qualification_manifest = json.loads((root / "qualification" / "manifest.json").read_bytes())
    assert len(verified_manifest["verified_500_instance_ids"]) == 500
    verified_ids = set(verified_manifest["verified_500_instance_ids"])
    qualification_ids = [task["instance_id"] for task in qualification_manifest["tasks"]]
    assert verified_ids.isdisjoint(qualification_ids)
    candidates = [f"qualification-{index:03d}" for index in range(10, 20)]
    ranked = sorted(
        candidates,
        key=lambda item: (
            hashlib.sha256(f"{freezer.QUALIFICATION_SELECTION_SEED}\0{item}".encode()).hexdigest(),
            item,
        ),
    )[:5]
    assert qualification_ids == sorted(ranked)
    qualification_policy = qualification_manifest["selection_policy"]
    assert qualification_policy == freezer._selection_policy(
        "qualification",
        "princeton-nlp/SWE-bench",
        prior_manifest_sha256="9" * 64,
        prior_exclusions_sha256=_sha256(Path(first_args.prior_qualification_exclusions)),
    )
    assert qualification_policy["candidate_split"] == "test"
    assert qualification_policy["exclusion"] == (
        "swebench_verified_500_and_prior_qualification_instance_ids"
    )
    assert qualification_policy["prior_qualification_manifest_sha256"] == "9" * 64
    assert qualification_policy["evidence_class"] == "qualification_only_non_capability"
    assert verified_manifest["selection_policy"] == freezer._selection_policy(
        "verified", "princeton-nlp/SWE-bench_Verified"
    )

    for path in [first_state, root, *root.rglob("*")]:
        expected = 0o700 if path.is_dir() else 0o600
        assert stat.S_IMODE(path.stat().st_mode) == expected
    for records in (root / "verified" / "records.jsonl", root / "qualification" / "records.jsonl"):
        for raw_line in records.read_bytes().splitlines():
            row = json.loads(raw_line)
            assert (
                raw_line
                == json.dumps(
                    row, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            )
            assert "extra_official_field" in row


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("source_hash", "source SHA-256"),
        ("revision", "40-hex"),
        ("verified_count", "exactly 500"),
        ("duplicate", "unique"),
        ("unsafe_id", "unsafe"),
        ("count_zero", "between 1 and 10"),
        ("count_eleven", "between 1 and 10"),
        ("missing_grader_field", "required official fields"),
        ("overlap_only", "disjoint"),
        ("prior_exclusions_hash", "prior qualification exclusions SHA-256"),
        ("prior_manifest_sha", "prior qualification manifest SHA-256"),
        ("prior_duplicate", "unique sorted"),
        ("prior_unknown", "exist in the parent source"),
    ],
)
def test_freeze_refuses_invalid_inputs(tmp_path: Path, mutation: str, message: str) -> None:
    args = _args(tmp_path)
    verified = Path(args.verified_source)
    parent = Path(args.parent_source)
    exclusions = Path(args.prior_qualification_exclusions)
    if mutation == "source_hash":
        args.verified_source_sha256 = "0" * 64
    elif mutation == "revision":
        args.parent_dataset_revision = "mutable-main"
    elif mutation == "verified_count":
        rows = [json.loads(line) for line in verified.read_text().splitlines()][:-1]
        _write_jsonl(verified, rows)
        args.verified_source_sha256 = _sha256(verified)
    elif mutation == "duplicate":
        rows = [json.loads(line) for line in verified.read_text().splitlines()]
        rows[-1]["instance_id"] = rows[0]["instance_id"]
        _write_jsonl(verified, rows)
        args.verified_source_sha256 = _sha256(verified)
    elif mutation == "unsafe_id":
        rows = [json.loads(line) for line in parent.read_text().splitlines()]
        rows[-1]["instance_id"] = "../private"
        _write_jsonl(parent, rows)
        args.parent_source_sha256 = _sha256(parent)
    elif mutation == "count_zero":
        args.qualification_count = 0
    elif mutation == "count_eleven":
        args.qualification_count = 11
    elif mutation == "missing_grader_field":
        rows = [json.loads(line) for line in parent.read_text().splitlines()]
        del rows[-1]["FAIL_TO_PASS"]
        _write_jsonl(parent, rows)
        args.parent_source_sha256 = _sha256(parent)
    elif mutation == "overlap_only":
        rows = [json.loads(line) for line in verified.read_text().splitlines()]
        _write_jsonl(parent, rows)
        args.parent_source_sha256 = _sha256(parent)
        _write_exclusions(exclusions, ["verified-001"])
        args.prior_qualification_exclusions_sha256 = _sha256(exclusions)
    elif mutation == "prior_exclusions_hash":
        args.prior_qualification_exclusions_sha256 = "0" * 64
    elif mutation == "prior_manifest_sha":
        _write_exclusions(
            exclusions, [f"qualification-{index:03d}" for index in range(10)], "invalid"
        )
        args.prior_qualification_exclusions_sha256 = _sha256(exclusions)
    elif mutation == "prior_duplicate":
        _write_exclusions(exclusions, ["qualification-000", "qualification-000"])
        args.prior_qualification_exclusions_sha256 = _sha256(exclusions)
    elif mutation == "prior_unknown":
        _write_exclusions(exclusions, ["unknown-qualification-000"])
        args.prior_qualification_exclusions_sha256 = _sha256(exclusions)

    with pytest.raises(freezer.FreezeError, match=message):
        freezer.freeze(args)
    assert not (Path(args.output_root) / "swebench").exists()
    assert not list(Path(args.output_root).glob(".swebench-freeze-stage-*"))


def test_freeze_refuses_symlinks_git_paths_and_overwrite(tmp_path: Path) -> None:
    args = _args(tmp_path / "symlink")
    link = tmp_path / "verified-link.jsonl"
    link.symlink_to(args.verified_source)
    args.verified_source = str(link)
    with pytest.raises(freezer.FreezeError, match="symbolic-link"):
        freezer.freeze(args)

    args = _args(tmp_path / "git")
    git_source = tmp_path / "git" / "checkout"
    (git_source / ".git").mkdir(parents=True)
    source = git_source / "verified.jsonl"
    source.write_bytes(Path(args.verified_source).read_bytes())
    args.verified_source = str(source)
    args.verified_source_sha256 = _sha256(source)
    with pytest.raises(freezer.FreezeError, match="outside Git"):
        freezer.freeze(args)

    args = _args(tmp_path / "overwrite")
    (Path(args.output_root) / "swebench").mkdir()
    with pytest.raises(freezer.FreezeError, match="overwrite"):
        freezer.freeze(args)


def test_main_stdout_contains_only_counts_and_hashes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args(tmp_path)
    argv = [
        "--repo-root",
        args.repo_root,
        "--output-root",
        args.output_root,
        "--verified-source",
        args.verified_source,
        "--verified-source-sha256",
        args.verified_source_sha256,
        "--verified-dataset-id",
        args.verified_dataset_id,
        "--verified-dataset-revision",
        args.verified_dataset_revision,
        "--parent-source",
        args.parent_source,
        "--parent-source-sha256",
        args.parent_source_sha256,
        "--parent-dataset-id",
        args.parent_dataset_id,
        "--parent-dataset-revision",
        args.parent_dataset_revision,
        "--prior-qualification-exclusions",
        args.prior_qualification_exclusions,
        "--prior-qualification-exclusions-sha256",
        args.prior_qualification_exclusions_sha256,
        "--license-authorization-receipt",
        args.license_authorization_receipt,
        "--license-authorization-revision",
        args.license_authorization_revision,
        "--qualification-count",
        str(args.qualification_count),
    ]
    assert freezer.main(argv) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "PRIVATE_" not in captured.out
    assert str(tmp_path) not in captured.out
    assert "verified-" not in captured.out
    payload = json.loads(captured.out)
    assert set(payload) == {"counts", "hashes", "status"}
    assert payload["status"] == "frozen"
    assert set(payload["hashes"]) == {
        "qualification_manifest_sha256",
        "qualification_records_sha256",
        "verified_manifest_sha256",
        "verified_records_sha256",
    }
