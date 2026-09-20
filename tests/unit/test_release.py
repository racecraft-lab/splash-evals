from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path
from runpy import run_path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECK_MEMBER = cast(
    Callable[[str], None], run_path(str(ROOT / "scripts" / "inspect_release.py"))["check_member"]
)


def test_release_member_policy_rejects_private_state_and_unsafe_paths() -> None:
    for member in ("package/.env.example", "package/runs/raw.json", "../outside.txt"):
        with pytest.raises(ValueError, match="unsafe archive"):
            CHECK_MEMBER(member)


def test_sdist_excludes_repository_only_and_private_template_paths() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    excluded = set(configuration["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"])

    assert {
        "/.env.example",
        "/.github",
        "/AGENTS.md",
        "/docs/github-feature-audit.md",
        "/tests",
    } <= excluded
