"""Small, dependency-free statistical helpers used by local reports."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def wilson_interval(
    successes: int, total: int, z: float = 1.959963984540054
) -> tuple[float, float]:
    """Return a two-sided Wilson score interval for a binary proportion."""
    if total <= 0:
        raise ValueError("total must be positive")
    if successes < 0 or successes > total:
        raise ValueError("successes must be between zero and total")
    p_hat = successes / total
    denominator = 1.0 + (z * z / total)
    centre = (p_hat + (z * z / (2.0 * total))) / denominator
    radius = (
        z
        * math.sqrt((p_hat * (1.0 - p_hat) / total) + (z * z / (4.0 * total * total)))
        / denominator
    )
    return max(0.0, centre - radius), min(1.0, centre + radius)


def summarize_binary(values: Iterable[bool | int]) -> dict[str, Any]:
    materialized = [bool(value) for value in values]
    total = len(materialized)
    successes = sum(materialized)
    if total == 0:
        return {
            "successes": 0,
            "total": 0,
            "rate": None,
            "wilson_95": None,
        }
    low, high = wilson_interval(successes, total)
    return {
        "successes": successes,
        "total": total,
        "rate": successes / total,
        "wilson_95": [low, high],
    }


def paired_binary_difference(
    left: Mapping[str, bool | int], right: Mapping[str, bool | int]
) -> dict[str, Any]:
    """Summarize right-minus-left on the intersection of task IDs."""
    matched_ids = sorted(set(left) & set(right))
    if not matched_ids:
        raise ValueError("paired comparison requires at least one matched task ID")
    differences = [int(bool(right[key])) - int(bool(left[key])) for key in matched_ids]
    return {
        "matched_count": len(matched_ids),
        "difference_right_minus_left": sum(differences) / len(differences),
        "left_only": sorted(set(left) - set(right)),
        "right_only": sorted(set(right) - set(left)),
    }


def cluster_bootstrap_difference(
    rows: Sequence[Mapping[str, Any]],
    *,
    cluster_key: str = "base_task_id",
    left_key: str = "left_score",
    right_key: str = "right_score",
    iterations: int = 2000,
    seed: int = 20260919,
) -> dict[str, Any]:
    """Bootstrap clusters and return a percentile interval for right-minus-left.

    Related repetitions remain in the same resampled cluster. The method assumes
    the supplied fixed task set is the population from which clusters are sampled.
    """
    if iterations < 100:
        raise ValueError("iterations must be at least 100")
    clusters: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        cluster = str(row[cluster_key])
        clusters[cluster].append(float(row[right_key]) - float(row[left_key]))
    if not clusters:
        raise ValueError("rows must contain at least one cluster")
    cluster_means = [sum(values) / len(values) for values in clusters.values()]
    observed = sum(cluster_means) / len(cluster_means)
    rng = random.Random(seed)  # noqa: S311 - reproducible statistical resampling, not security.
    estimates: list[float] = []
    for _ in range(iterations):
        sampled = [rng.choice(cluster_means) for _ in cluster_means]
        estimates.append(sum(sampled) / len(sampled))
    estimates.sort()
    lower_index = max(0, math.floor(0.025 * (iterations - 1)))
    upper_index = min(iterations - 1, math.ceil(0.975 * (iterations - 1)))
    return {
        "difference_right_minus_left": observed,
        "cluster_count": len(cluster_means),
        "bootstrap_iterations": iterations,
        "interval_95": [estimates[lower_index], estimates[upper_index]],
        "assumption": "task-cluster bootstrap over the supplied fixed task set",
    }
