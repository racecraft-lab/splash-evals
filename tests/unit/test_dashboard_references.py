"""Keep reader-facing results tied to reviewed public records and source references."""

import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _section(text: str, heading: str) -> str:
    return text.split(f"## {heading}", 1)[1].split("</section>", 1)[0]


def _row(block: str, label: str) -> list[str]:
    line = next(
        line
        for line in block.splitlines()
        if line.startswith("| ") and label in line.split("|", 2)[1]
    )
    return [cell.strip() for cell in line.split("|")[1:-1]]


def test_dashboard_splash_row_matches_public_gpqa_result():
    result = json.loads(
        (ROOT / "results/public/gpqa-diamond-splash-local-2026-09-20.json").read_text()
    )
    dashboard = (ROOT / "docs/dashboard.md").read_text()
    comparison = _section(dashboard, "Benchmark comparison")
    line = next(
        line
        for line in comparison.splitlines()
        if "Splash / Qwen3.8 · local LM Studio · medium effort" in line
    )
    cells = [cell.strip().strip("*") for cell in line.split("|")[1:-1]]

    displayed_score = (Decimal(str(result["benchmark"]["score"])) * 100).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    assert cells[1] == f"{displayed_score}%"
    assert cells[2] == (
        f"Measured here; {result['benchmark']['succeeded_cases']}"
        f"/{result['benchmark']['requested_cases']} completed"
    )
    assert (
        f"{result['benchmark']['requested_cases']} requested, "
        f"{result['benchmark']['succeeded_cases']} succeeded, "
        f"{result['benchmark']['errored_cases']} errored"
    ) in dashboard
    assert f"{result['performance']['latency_seconds']['average']:.2f} s" in dashboard
    assert f"{result['performance']['average_output_tokens_per_second']:.2f} tokens/s" in dashboard


@pytest.mark.parametrize(
    ("record", "row_label"),
    [
        (
            "openai-gpt-5.6-sol-2026-07-gpqa",
            "GPT-5.6 Sol",
        ),
        (
            "openai-gpt-5.6-terra-2026-07-gpqa",
            "GPT-5.6 Terra",
        ),
        (
            "openai-gpt-5.6-luna-2026-07-gpqa",
            "GPT-5.6 Luna",
        ),
        (
            "openai-gpt-5.5-2026-07-gpqa",
            "GPT-5.5",
        ),
    ],
)
def test_visible_historical_gpqa_scores_match_catalog(record, row_label):
    data = yaml.safe_load((ROOT / f"references/frontier/{record}.yaml").read_text())
    text = (ROOT / "docs/dashboard.md").read_text()
    row = _row(_section(text, "Benchmark comparison"), row_label)

    assert row[1] == f"{data['reported_score']:.1f}%"
    assert data["source_url"] in (ROOT / "docs/sources.md").read_text()


@pytest.mark.parametrize(
    "record",
    [
        "gpt-4.1-2025-04-gpqa",
        "gpt-4o-2024-11-20-gpqa",
        "openai-o1-high-gpqa",
    ],
)
def test_retired_context_records_remain_in_verification_ledger(record):
    data = yaml.safe_load((ROOT / f"references/frontier/{record}.yaml").read_text())
    ledger = (ROOT / "references/frontier/VERIFICATION.md").read_text()

    assert f"`{data['reference_id']}`" in ledger
    assert f"{data['reported_score']:.1f}%" in ledger
    assert data["source_url"] in (ROOT / "docs/sources.md").read_text()


def test_unspecified_gpqa_split_is_not_presented_as_diamond():
    dashboard = (ROOT / "docs/dashboard.md").read_text()
    data = yaml.safe_load(
        (ROOT / "references/frontier/claude-3.5-sonnet-2024-06-gpqa.yaml").read_text()
    )

    assert data["benchmark"]["split"] is None
    assert f"{data['reported_score']:.1f}%" not in _section(dashboard, "Benchmark comparison")
