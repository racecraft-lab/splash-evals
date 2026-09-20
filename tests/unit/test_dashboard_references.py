"""Keep reader-facing scores tied to the reviewed historical catalog."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("record", "section", "row_label"),
    [
        ("gpt-4o-2024-11-20-gpqa", "Scientific reasoning", "GPT-4o"),
        ("gpt-4.1-2025-04-gpqa", "Scientific reasoning", "GPT-4.1"),
        ("openai-o1-high-gpqa", "Scientific reasoning", "OpenAI o1 (high)"),
        ("gpt-4o-2024-11-20-ifeval", "Instruction following", "GPT-4o"),
        ("gpt-4.1-2025-04-ifeval", "Instruction following", "GPT-4.1"),
        ("openai-o1-high-ifeval", "Instruction following", "OpenAI o1 (high)"),
        ("claude-3.5-sonnet-20241022-aider-pass1", "Coding", "Claude 3.5 Sonnet · 2024-10-22"),
        ("claude-3.5-sonnet-20241022-aider-pass2", "Coding", "Claude 3.5 Sonnet · 2024-10-22"),
        (
            "claude-3.7-sonnet-20250219-no-think-aider-pass1",
            "Coding",
            "Claude 3.7 Sonnet · 2025-02-19 · no thinking",
        ),
        (
            "claude-3.7-sonnet-20250219-no-think-aider-pass2",
            "Coding",
            "Claude 3.7 Sonnet · 2025-02-19 · no thinking",
        ),
        (
            "claude-3.7-sonnet-20250219-think32k-aider-pass1",
            "Coding",
            "Claude 3.7 Sonnet · 2025-02-19 · 32K thinking",
        ),
        (
            "claude-3.7-sonnet-20250219-think32k-aider-pass2",
            "Coding",
            "Claude 3.7 Sonnet · 2025-02-19 · 32K thinking",
        ),
        ("gemini-2.5-pro-preview-03-25-aider-pass1", "Coding", "Gemini 2.5 Pro Preview 03-25"),
        ("gemini-2.5-pro-preview-03-25-aider-pass2", "Coding", "Gemini 2.5 Pro Preview 03-25"),
    ],
)
def test_dashboard_score_matches_catalog(record, section, row_label):
    data = yaml.safe_load((ROOT / f"references/frontier/{record}.yaml").read_text())
    text = (ROOT / "docs/dashboard.md").read_text()
    block = text.split(f"### {section}", 1)[1].split("</section>", 1)[0]
    row = next(line for line in block.splitlines() if line.startswith(f"| {row_label} |"))
    cells = [cell.strip() for cell in row.split("|")[1:-1]]
    score_column = 2 if data["benchmark"]["metric_name"] == "success_after_permitted_repair" else 1
    assert cells[score_column] == f"{data['reported_score']:.1f}%"
    assert cells[-1] == "Not yet measured"
    assert data["source_url"] in block
    evidence_date = data["measurement_date"] or data["source_publication_date"]
    assert evidence_date in row


def test_unspecified_gpqa_split_is_separate_from_diamond():
    text = (ROOT / "docs/dashboard.md").read_text()
    data = yaml.safe_load(
        (ROOT / "references/frontier/claude-3.5-sonnet-2024-06-gpqa.yaml").read_text()
    )
    assert data["benchmark"]["split"] is None
    note = text.split("**Separate, unresolved split:**", 1)[1].split("</section>", 1)[0]
    assert f"{data['reported_score']:.1f}%" in note
    assert data["source_url"] in note
    assert "deliberately excluded from the Diamond table" in note
