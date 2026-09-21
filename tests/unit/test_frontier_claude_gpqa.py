from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from local_evals.frontier import validate_reference_record

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "references" / "frontier"

EXPECTED: dict[str, tuple[str, str, float, str]] = {
    "anthropic-claude-opus-4-2025-05-gpqa.yaml": (
        "Claude Opus 4",
        "2025-05",
        76,
        "fecdf541f8eae11cab26bc9ec54829326acefbcbbe2dd811a76b8da8bc84010c",
    ),
    "anthropic-claude-sonnet-4-2025-05-gpqa.yaml": (
        "Claude Sonnet 4",
        "2025-05",
        78,
        "2831eb5b017e7c20b14cf51ca84ee43084e76551211cdb1e6c7a2cb1ecc33d12",
    ),
    "anthropic-claude-sonnet-4-6-2026-02-gpqa.yaml": (
        "Claude Sonnet 4.6",
        "2026-02",
        87,
        "8e4729afad0f3bdf2c36b2339fa9460a9b4725f3424cdeeb7ba37631fce93dbd",
    ),
    "anthropic-claude-opus-4-6-2026-02-gpqa.yaml": (
        "Claude Opus 4.6",
        "2026-02",
        91,
        "689916c0d30c1b9207725bf6665c428285706c300da2502ca518d5c702ccfbd7",
    ),
    "anthropic-claude-opus-4-7-2026-04-gpqa.yaml": (
        "Claude Opus 4.7",
        "2026-04",
        90,
        "75ea91835df7171a257c31013213d799ae7ea7ffa672d24e84a4bb081dd9fc01",
    ),
    "anthropic-claude-opus-4-8-2026-05-gpqa.yaml": (
        "Claude Opus 4.8",
        "2026-05",
        91,
        "aeae24437fd2442a9add5475bd2bbe34f6fe6fe94bc3f0afc98582c650557fe1",
    ),
    "anthropic-claude-sonnet-5-2026-06-gpqa.yaml": (
        "Claude Sonnet 5",
        "2026-06",
        91,
        "86b52d067961cff58742a6d0eb36da6a8da0ef2d7caf9ea05895b9ede2315162",
    ),
    "anthropic-claude-opus-5-2026-07-gpqa.yaml": (
        "Claude Opus 5",
        "2026-07",
        94,
        "5fb715ee67e16518b3b48ae3ea811517e8928323bde7ffaa13ac564c2681d19c",
    ),
}


def _load(filename: str) -> dict[str, Any]:
    value = yaml.safe_load((CATALOG / filename).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize(("filename", "expected"), EXPECTED.items())
def test_epoch_claude_gpqa_record_is_a_valid_incompatible_reference(
    filename: str, expected: tuple[str, str, float, str]
) -> None:
    name, release_period, score, source_hash = expected
    record = _load(filename)

    assert validate_reference_record(record, source_name=filename)["valid"] is True
    assert record["provider"] == "Anthropic"
    assert record["model_display_name"] == name
    assert record["release_period"] == release_period
    assert record["reported_score"] == score
    assert record["source_url"] == (
        "https://epoch.ai/models/" + filename.removeprefix("anthropic-").split("-202", 1)[0]
    )
    assert record["source_revision_or_content_hash"].endswith("sha256:" + source_hash)
    assert record["retrieved_on"] == "2026-09-21"
    assert record["benchmark"] == {
        "name": "GPQA Diamond",
        "version": None,
        "split": "Diamond",
        "dataset_revision": None,
        "sample_count": None,
        "sample_id_manifest": None,
        "metric_name": "accuracy",
        "metric_unit": "percent",
        "higher_is_better": True,
    }
    assert all(value is None for value in record["protocol"].values())
    assert record["evidence_class"] == "context_only_incompatible_reference"
    assert record["comparability"] == "incompatible"
    notes = " ".join(record["comparability_notes"])
    assert "rounded whole-percent" in notes
    assert "Protocol equivalence is not established" in notes
    assert "must not support a direct score difference" in notes


def test_requested_epoch_claude_roster_is_exact_and_has_unique_ids() -> None:
    paths = sorted(CATALOG.glob("anthropic-claude-*-gpqa.yaml"))
    assert {path.name for path in paths} == set(EXPECTED)

    records = [_load(path.name) for path in paths]
    assert len({record["reference_id"] for record in records}) == len(records)
