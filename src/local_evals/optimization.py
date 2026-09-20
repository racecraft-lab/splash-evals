"""Bounded calibration-only optimization with a sealed held-out pilot."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .runs import (
    RunError,
    execute_run,
    get_state_dir,
    load_config,
    load_policies,
    load_run,
    project_root,
    suite_tasks,
)


class OptimizationRefused(RunError):
    """The proposed optimization violates the bounded experiment contract."""


def _hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_matrix(name: str, root: Path) -> dict[str, Any]:
    if name != Path(name).name or any(part in name for part in ("/", "\\", "..")):
        raise OptimizationRefused("matrix must be a simple identifier")
    path = root / "configs" / "tuning" / f"{name}.yaml"
    if not path.is_file():
        raise OptimizationRefused(f"optimization matrix not found: {name}")
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise OptimizationRefused("optimization matrix must be a mapping")
    return value


def build_optimization_manifest(
    matrix_name: str,
    baseline_name: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    repo = root or project_root()
    matrix = _load_matrix(matrix_name, repo)
    baseline = load_config(baseline_name, repo)
    candidates = matrix.get("candidates")
    if not isinstance(candidates, list):
        raise OptimizationRefused("matrix candidates must be a list")
    limit = int(load_policies(repo)["initial_run_limits"]["max_tuning_candidates_beyond_baseline"])
    if len(candidates) > limit or len(candidates) > 3:
        raise OptimizationRefused("optimization is limited to three candidates beyond baseline")
    ids = [str(item.get("id")) for item in candidates if isinstance(item, dict)]
    if len(ids) != len(candidates) or len(set(ids)) != len(ids):
        raise OptimizationRefused("every candidate needs a unique ID")
    calibration = suite_tasks("calibration")
    held_out = suite_tasks("pilot")
    calibration_ids = [task.task_id for task in calibration]
    held_out_ids = [task.task_id for task in held_out]
    if set(calibration_ids) & set(held_out_ids):
        raise OptimizationRefused("calibration and held-out task IDs are not disjoint")
    output_cap = 256
    candidate_records = [
        {
            "id": "baseline",
            "config_id": baseline_name,
            "delta": {},
            "reason": "Immutable as-found baseline.",
            "estimated_token_allowance": len(calibration_ids) * output_cap,
        }
    ]
    for item in candidates:
        if not isinstance(item, dict) or not isinstance(item.get("delta"), dict):
            raise OptimizationRefused("each candidate must provide an explicit delta mapping")
        candidate_records.append(
            {
                "id": item["id"],
                "config_id": baseline_name,
                "delta": item["delta"],
                "reason": item.get("reason"),
                "estimated_token_allowance": len(calibration_ids) * output_cap,
            }
        )
    optimization_id = _hash(
        {
            "matrix": matrix,
            "baseline_config": baseline,
            "calibration_ids": calibration_ids,
            "held_out_ids": held_out_ids,
        }
    )[:20]
    return {
        "schema_version": 1,
        "optimization_id": optimization_id,
        "matrix": matrix_name,
        "baseline": baseline_name,
        "objective": matrix.get("objective"),
        "candidate_count_beyond_baseline": len(candidates),
        "candidates": candidate_records,
        "calibration": {
            "task_set": "builtin-calibration-v1",
            "sample_count": len(calibration_ids),
            "sample_ids": calibration_ids,
            "selection_hash": _hash(calibration_ids),
        },
        "held_out": {
            "task_set": "builtin-heldout-pilot-v1",
            "sample_count": len(held_out_ids),
            "sample_ids": held_out_ids,
            "selection_hash": _hash(held_out_ids),
            "state": "sealed_until_candidate_selection",
            "used_for_selection": False,
        },
        "estimated_token_allowance": len(candidate_records) * len(calibration_ids) * output_cap,
        "stop_conditions": [
            "shared session request, token, or wall-time budget reached",
            "server/model crash or transport failure",
            "configuration rejection or unverifiable critical setting",
            "new deterministic contract regression",
            "context truncation or output censoring",
        ],
        "selection_rule": (
            "Preserve calibration correctness/reliability, then minimize complete-answer "
            "latency; tiny-sample result remains provisional."
        ),
        "baseline_config_sha256": _hash(baseline),
    }


def _metrics(run_id: str, root: Path) -> dict[str, Any]:
    manifest, attempts = load_run(run_id, root)
    aggregate = manifest["aggregate"]
    latencies = [
        float(item["elapsed_seconds"])
        for item in attempts
        if item.get("elapsed_seconds") is not None and item.get("score", {}).get("score") == 1
    ]
    return {
        "run_id": run_id,
        "capability_rate": aggregate["capability_conditional_on_valid_execution"]["rate"],
        "deployment_success_rate": aggregate["end_to_end_deployment_success"]["rate"],
        "failed": aggregate["failed"],
        "censored": aggregate["censored"],
        "mean_success_latency_seconds": (sum(latencies) / len(latencies)) if latencies else None,
        "status": manifest["status"],
    }


def _selection_key(item: dict[str, Any]) -> tuple[float, float, float]:
    capability = item["metrics"]["capability_rate"]
    reliability = item["metrics"]["deployment_success_rate"]
    latency = item["metrics"]["mean_success_latency_seconds"]
    return (
        float(capability) if capability is not None else -1.0,
        float(reliability) if reliability is not None else -1.0,
        -float(latency) if latency is not None else float("-inf"),
    )


def optimize(
    matrix_name: str,
    baseline_name: str,
    *,
    dry_run: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    repo = root or project_root()
    manifest = build_optimization_manifest(matrix_name, baseline_name, root=repo)
    if dry_run:
        return {"status": "dry_run", "manifest": manifest}
    state = get_state_dir(repo)
    output_dir = state / "optimizations" / manifest["optimization_id"]
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest["status"] = "running"
    manifest["started_at"] = datetime.now(UTC).isoformat()
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(output_dir / "manifest.json", 0o600)
    results: list[dict[str, Any]] = []
    for candidate in manifest["candidates"]:
        outcome = execute_run(
            "calibration",
            baseline_name,
            root=repo,
            operation_override=candidate["delta"],
            run_label=f"opt-{manifest['optimization_id']}-{candidate['id']}",
        )
        metrics = _metrics(outcome["run_id"], repo)
        results.append({"candidate": candidate, "metrics": metrics})
        if metrics["status"] != "completed":
            manifest["stop_reason"] = f"candidate {candidate['id']} did not complete"
            break
    if not results:
        raise OptimizationRefused("no calibration result was produced")
    baseline_result = results[0]
    eligible = []
    base_capability = baseline_result["metrics"]["capability_rate"]
    base_reliability = baseline_result["metrics"]["deployment_success_rate"]
    for item in results:
        metrics = item["metrics"]
        item["eligible"] = (
            metrics["status"] == "completed"
            and metrics["censored"] == 0
            and metrics["capability_rate"] is not None
            and metrics["deployment_success_rate"] is not None
            and (base_capability is None or metrics["capability_rate"] >= base_capability)
            and (base_reliability is None or metrics["deployment_success_rate"] >= base_reliability)
        )
        if item["eligible"]:
            eligible.append(item)
    selected = max(eligible or [baseline_result], key=_selection_key)
    baseline_config = load_config(baseline_name, repo)
    frozen = json.loads(json.dumps(baseline_config))
    frozen_id = f"frozen-{manifest['optimization_id']}"
    frozen["id"] = frozen_id
    frozen["status"] = "frozen_provisional"
    frozen["operation_requested"] = {
        **(frozen.get("operation_requested") or {}),
        **selected["candidate"]["delta"],
    }
    frozen["selection_evidence"] = {
        "optimization_id": manifest["optimization_id"],
        "calibration_run_id": selected["metrics"]["run_id"],
        "held_out_used_for_selection": False,
        "held_out_selection_hash": manifest["held_out"]["selection_hash"],
        "limitations": (
            "Selected on 24 synthetic calibration tasks; held-out validation is still required."
        ),
    }
    frozen_dir = state / "frozen_profiles"
    frozen_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    frozen_path = frozen_dir / f"{frozen_id}.json"
    frozen_path.write_text(json.dumps(frozen, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(frozen_path, 0o600)
    manifest.update(
        {
            "status": "completed",
            "completed_at": datetime.now(UTC).isoformat(),
            "results": results,
            "selected_candidate": selected["candidate"]["id"],
            "frozen_config_id": frozen_id,
            "held_out": {
                **manifest["held_out"],
                "state": "frozen_unopened",
                "used_for_selection": False,
            },
            "limitations": [
                "Candidate selection is provisional because the calibration set is small.",
                "The frozen candidate has not passed held-out validation until a separate "
                "pilot run completes.",
                "Accepted request settings are not assumed effective without independent "
                "read-back evidence.",
            ],
        }
    )
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "status": "completed",
        "optimization_id": manifest["optimization_id"],
        "frozen_config_id": frozen_id,
        "manifest": manifest,
    }
