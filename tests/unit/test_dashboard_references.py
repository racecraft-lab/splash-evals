"""Keep reader-facing results tied to reviewed public records and source references."""

import json
import re
from decimal import ROUND_HALF_UP, Decimal
from html import unescape
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GPQA_DETAIL = ROOT / "docs-site/src/content/docs/dashboard/gpqa-diamond.md"


def _row(table: str, label: str) -> list[str]:
    row = next(
        match.group(0)
        for match in re.finditer(r"<tr\b[^>]*>.*?</tr>", table, re.DOTALL)
        if label in match.group(0)
    )
    return [
        unescape(re.sub(r"<[^>]+>", "", cell)).strip()
        for cell in re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.DOTALL)
    ]


def test_gpqa_detail_splash_row_matches_public_result():
    result = json.loads(
        (ROOT / "results/public/gpqa-diamond-splash-local-2026-09-20.json").read_text()
    )
    gpqa_detail = GPQA_DETAIL.read_text()
    cells = _row(gpqa_detail, "Splash / Qwen3.8")

    displayed_score = (Decimal(str(result["benchmark"]["score"])) * 100).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    assert cells[1] == f"{displayed_score}%"
    assert cells[2] == "Measured here"
    assert cells[3] == (
        f"{result['benchmark']['succeeded_cases']}"
        f"/{result['benchmark']['requested_cases']} completed · "
        f"{result['benchmark']['errored_cases']} errors"
    )
    assert (
        f"{result['benchmark']['requested_cases']} requested, "
        f"{result['benchmark']['succeeded_cases']} succeeded, "
        f"{result['benchmark']['errored_cases']} errored"
    ) in gpqa_detail
    assert f"{result['performance']['latency_seconds']['average']:.2f} s" in gpqa_detail
    assert (
        f"{result['performance']['average_output_tokens_per_second']:.2f} tokens/s" in gpqa_detail
    )


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
    row = _row(GPQA_DETAIL.read_text(), row_label)

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
    gpqa_detail = GPQA_DETAIL.read_text()
    data = yaml.safe_load(
        (ROOT / "references/frontier/claude-3.5-sonnet-2024-06-gpqa.yaml").read_text()
    )

    assert data["benchmark"]["split"] is None
    assert f"{data['reported_score']:.1f}%" not in gpqa_detail
