from __future__ import annotations

from pathlib import Path

import pytest

import local_evals.optimization as optimization
from local_evals.optimization import OptimizationRefused, build_optimization_manifest
from local_evals.runs import Task


def _patch_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    candidates: list[dict[str, object]],
    calibration_ids: tuple[str, ...] = ("calibration-a", "calibration-b"),
    held_out_ids: tuple[str, ...] = ("held-out-a",),
) -> None:
    monkeypatch.setattr(
        optimization,
        "_load_matrix",
        lambda name, root: {
            "objective": "synthetic bounded objective",
            "candidates": candidates,
        },
    )
    monkeypatch.setattr(optimization, "load_config", lambda name, root=None: {"id": name})
    monkeypatch.setattr(
        optimization,
        "load_policies",
        lambda root=None: {"initial_run_limits": {"max_tuning_candidates_beyond_baseline": 3}},
    )

    def suite_tasks(name: str) -> list[Task]:
        ids = calibration_ids if name == "calibration" else held_out_ids
        return [Task(task_id, name, "synthetic", "exact", "synthetic") for task_id in ids]

    monkeypatch.setattr(optimization, "suite_tasks", suite_tasks)


def test_optimization_refuses_more_than_three_candidates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_dependencies(
        monkeypatch,
        candidates=[{"id": f"candidate-{index}", "delta": {}} for index in range(4)],
    )

    with pytest.raises(OptimizationRefused, match="limited to three"):
        build_optimization_manifest("bounded", "lmstudio-as-found", root=tmp_path)


def test_optimization_refuses_calibration_held_out_overlap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_dependencies(
        monkeypatch,
        candidates=[{"id": "candidate-a", "delta": {"temperature": 0.7}}],
        calibration_ids=("shared-task",),
        held_out_ids=("shared-task",),
    )

    with pytest.raises(OptimizationRefused, match="not disjoint"):
        build_optimization_manifest("bounded", "lmstudio-as-found", root=tmp_path)


def test_manifest_keeps_held_out_selection_sealed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_dependencies(
        monkeypatch,
        candidates=[{"id": "candidate-a", "delta": {"temperature": 0.7}}],
    )

    manifest = build_optimization_manifest("bounded", "lmstudio-as-found", root=tmp_path)

    assert manifest["candidate_count_beyond_baseline"] == 1
    assert manifest["candidates"][0]["id"] == "baseline"
    assert manifest["held_out"]["state"] == "sealed_until_candidate_selection"
    assert manifest["held_out"]["used_for_selection"] is False
    assert set(manifest["calibration"]["sample_ids"]).isdisjoint(manifest["held_out"]["sample_ids"])
    assert any("shared session" in condition for condition in manifest["stop_conditions"])
