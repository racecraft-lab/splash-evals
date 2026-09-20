from __future__ import annotations

import re

from typer.testing import CliRunner

import local_evals.cli as cli
from local_evals.cli import app


def test_cli_help_exposes_required_command_groups_without_side_effects() -> None:
    result = CliRunner().invoke(
        app,
        ["--help"],
        color=False,
        terminal_width=240,
        env={"COLUMNS": "240", "FORCE_COLOR": None, "NO_COLOR": "1"},
    )
    help_text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.stdout)

    assert result.exit_code == 0
    for command in (
        "doctor",
        "lmstudio",
        "plan",
        "run",
        "optimize",
        "compare",
        "settings",
        "view",
        "resume",
        "rescore",
        "frontier",
        "privacy",
        "publish",
    ):
        assert command in help_text


def test_publish_prepare_help_requires_explicit_run_and_supports_dry_run() -> None:
    result = CliRunner().invoke(
        app,
        ["publish", "prepare", "--help"],
        color=False,
        terminal_width=240,
        env={"COLUMNS": "240", "FORCE_COLOR": None, "NO_COLOR": "1"},
    )
    help_text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.stdout)

    assert result.exit_code == 0
    assert "--run" in help_text
    assert "--dry-run" in help_text


def test_expanded_run_requires_explicit_authorization_flag_in_interface() -> None:
    result = CliRunner().invoke(
        app,
        ["run", "--help"],
        color=False,
        terminal_width=240,
        env={"COLUMNS": "240", "FORCE_COLOR": None, "NO_COLOR": "1"},
    )
    help_text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.stdout)

    assert result.exit_code == 0
    assert "--allow-expanded" in help_text


def test_frontier_validate_default_reports_missing_coverage_without_failing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "validate_catalog",
        lambda catalog: {
            "schema_valid": True,
            "coverage_gate_met": False,
            "valid": False,
            "results": [],
        },
    )

    result = CliRunner().invoke(
        app,
        ["frontier", "validate", "--catalog", "references/frontier"],
    )

    assert result.exit_code == 0
    assert '"coverage_gate_met": false' in result.stdout
    assert '"valid": false' in result.stdout


def test_frontier_validate_require_coverage_fails_when_coverage_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "validate_catalog",
        lambda catalog: {
            "schema_valid": True,
            "coverage_gate_met": False,
            "valid": False,
            "results": [],
        },
    )

    result = CliRunner().invoke(
        app,
        [
            "frontier",
            "validate",
            "--catalog",
            "references/frontier",
            "--require-coverage",
        ],
    )

    assert result.exit_code == 2
    assert '"coverage_gate_met": false' in result.stdout
    assert '"valid": false' in result.stdout


def test_frontier_validate_default_still_fails_schema_or_provenance_errors(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "validate_catalog",
        lambda catalog: {
            "schema_valid": False,
            "coverage_gate_met": False,
            "valid": False,
            "results": [{"valid": False, "errors": ["synthetic provenance failure"]}],
        },
    )

    result = CliRunner().invoke(
        app,
        ["frontier", "validate", "--catalog", "references/frontier"],
    )

    assert result.exit_code == 2
    assert "synthetic provenance failure" in result.stdout
