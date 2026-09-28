"""Export validated public benchmark records for the static documentation build.

The Node build checks every input hash. Python CI checks this export against the
shared models, so site builds need no model access or private runtime state.
"""

from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml

from .benchmark_results import (
    BenchmarkResult,
    ReferenceObservation,
    adapt_gpqa_public_result,
)
from .frontier import validate_reference_record

EXPORT_PATH = "docs-site/src/data/benchmark-records.json"
GPQA_PATH = "results/public/gpqa-diamond-splash-local-2026-09-20.json"
BENCHMARK_NAMES = ("GPQA Diamond", "SWE-bench Verified")


def public_inputs(root: Path) -> list[Path]:
    return sorted([*root.glob("results/public/*.json"), *root.glob("references/frontier/*.yaml")])


def _reference(payload: dict[str, Any], digest: str) -> ReferenceObservation:
    result = validate_reference_record(payload)
    if not result["valid"]:
        raise ValueError(f"Invalid frontier reference: {payload.get('reference_id')}")
    benchmark = payload["benchmark"]
    return ReferenceObservation(
        reference_id=payload["reference_id"],
        source_record_id=payload["reference_id"],
        source_record_sha256=digest,
        model_label=payload["model_display_name"],
        denominator=benchmark.get("sample_count"),
        score=payload.get("reported_score"),
        metric_name=benchmark["metric_name"],
        metric_unit=benchmark["metric_unit"],
        source_url=payload["source_url"],
        source_locator=payload.get("source_locator"),
        evaluator=payload.get("evaluator"),
        developer=payload["provider"],
        evaluator_class=payload.get("evaluator_class"),
        limitations=tuple(payload["comparability_notes"]),
    )


def build_site_records(root: Path) -> dict[str, Any]:
    """Build deterministic records exclusively from the public source tree."""
    root = root.resolve()
    hashes: dict[str, str] = {}
    references: dict[str, list[ReferenceObservation]] = {name: [] for name in BENCHMARK_NAMES}
    local_results: dict[str, BenchmarkResult | None] = {name: None for name in BENCHMARK_NAMES}
    for path in public_inputs(root):
        if path.is_symlink() or root not in path.resolve().parents:
            raise ValueError("Public benchmark inputs must be regular repository files")
        data = path.read_bytes()
        relative = path.relative_to(root).as_posix()
        hashes[relative] = sha256(data).hexdigest()
        payload = yaml.safe_load(data) if path.suffix == ".yaml" else json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError(f"Public benchmark input must be an object: {relative}")
        if path.suffix == ".yaml":
            name = payload.get("benchmark", {}).get("name")
            if name in references and payload.get("verification_status") == "source_verified":
                references[name].append(_reference(payload, hashes[relative]))
        elif relative == GPQA_PATH:
            if local_results["GPQA Diamond"] is not None:
                raise ValueError("Multiple public results for GPQA Diamond")
            local_results["GPQA Diamond"] = adapt_gpqa_public_result(payload)
        else:
            candidate = payload.get("benchmark_result", payload)
            if not isinstance(candidate, dict):
                raise ValueError(f"Public benchmark result must be an object: {relative}")
            name = candidate.get("identity", {}).get("name")
            if name not in local_results:
                continue
            if local_results[name] is not None:
                raise ValueError(
                    f"Multiple public results for {name}; select a reviewed result first"
                )
            local_results[name] = BenchmarkResult.model_validate(candidate)
    if local_results["GPQA Diamond"] is None:
        raise ValueError("Reviewed GPQA artifact is required")
    benchmarks: dict[str, Any] = {}
    for name in BENCHMARK_NAMES:
        ids = [item.reference_id for item in references[name]]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate reference IDs for {name}")
        record = local_results[name]
        if record is not None:
            record = BenchmarkResult.model_validate(
                {**record.model_dump(), "reference_observations": tuple(references[name])}
            )
        benchmarks[name] = {
            "result": record.model_dump(mode="json") if record else None,
            "references": [item.model_dump(mode="json") for item in references[name]],
        }
    return {"schema_version": 1, "input_sha256": hashes, "benchmarks": benchmarks}


def serialized_site_records(root: Path) -> str:
    return json.dumps(build_site_records(root), indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--write", action="store_true", help="Regenerate the validated export")
    args = parser.parse_args()
    expected = serialized_site_records(args.root)
    target = args.root / EXPORT_PATH
    if args.write:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(expected, encoding="utf-8")
    elif not target.exists() or target.read_text(encoding="utf-8") != expected:
        raise SystemExit(
            "Validated site records are stale; run python -m local_evals.site_records --write"
        )


if __name__ == "__main__":
    main()
