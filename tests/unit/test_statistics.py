from __future__ import annotations

import pytest

from local_evals.statistics import (
    cluster_bootstrap_difference,
    paired_binary_difference,
    summarize_binary,
    wilson_interval,
)


@pytest.mark.parametrize("successes,total", [(-1, 1), (2, 1), (0, 0)])
def test_wilson_interval_rejects_invalid_counts(successes: int, total: int) -> None:
    with pytest.raises(ValueError):
        wilson_interval(successes, total)


def test_empty_binary_summary_uses_null_not_zero_rate() -> None:
    assert summarize_binary([]) == {
        "successes": 0,
        "total": 0,
        "rate": None,
        "wilson_95": None,
    }


def test_wilson_interval_contains_observed_rate() -> None:
    low, high = wilson_interval(7, 10)
    assert 0.0 <= low <= 0.7 <= high <= 1.0


def test_paired_difference_uses_only_matched_task_ids() -> None:
    result = paired_binary_difference(
        {"task-a": False, "task-b": True},
        {"task-a": True, "task-c": True},
    )

    assert result["matched_count"] == 1
    assert result["difference_right_minus_left"] == 1.0
    assert result["left_only"] == ["task-b"]
    assert result["right_only"] == ["task-c"]


def test_paired_difference_refuses_disjoint_runs() -> None:
    with pytest.raises(ValueError, match="matched task ID"):
        paired_binary_difference({"task-a": True}, {"task-b": True})


def test_cluster_bootstrap_is_deterministic_and_clustered() -> None:
    rows = [
        {"base_task_id": "seed-a", "left_score": 0, "right_score": 1},
        {"base_task_id": "seed-a", "left_score": 1, "right_score": 1},
        {"base_task_id": "seed-b", "left_score": 1, "right_score": 0},
    ]

    first = cluster_bootstrap_difference(rows, iterations=200, seed=17)
    second = cluster_bootstrap_difference(rows, iterations=200, seed=17)

    assert first == second
    assert first["cluster_count"] == 2
    assert first["bootstrap_iterations"] == 200
    assert "task-cluster" in first["assumption"]
