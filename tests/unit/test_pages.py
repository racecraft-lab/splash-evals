from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path
from runpy import run_path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILD_PAGES = run_path(str(ROOT / "scripts" / "build_pages.py"))
BUILD_PAGES_MAIN = cast(Callable[[], int], BUILD_PAGES["main"])
INLINE = cast(Callable[[str], str], BUILD_PAGES["inline"])


def test_pages_build_rewrites_relative_markdown_links_to_existing_html(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    docs = tmp_path / "docs"
    references = tmp_path / "references" / "frontier"
    docs.mkdir(parents=True)
    references.mkdir(parents=True)
    (docs / "index.md").write_text(
        "\n".join(
            (
                "# Synthetic documentation",
                "",
                "- [Methodology](methodology.md#scope)",
                "- [Verification](../references/frontier/VERIFICATION.md)",
                "- [Published source](https://example.com/source.md#published)",
                "- [Local section](#local-section)",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (docs / "methodology.md").write_text("# Scope\n", encoding="utf-8")
    (references / "VERIFICATION.md").write_text("# Verification\n", encoding="utf-8")
    output = tmp_path / "site"
    monkeypatch.setitem(BUILD_PAGES_MAIN.__globals__, "ROOT", tmp_path)
    monkeypatch.setitem(BUILD_PAGES_MAIN.__globals__, "ALLOWED_ROOTS", (docs, references))
    monkeypatch.setitem(BUILD_PAGES_MAIN.__globals__, "ALLOWED_TOP_LEVEL", ())
    monkeypatch.setattr(sys, "argv", ["build_pages.py", "--output", str(output)])

    assert BUILD_PAGES_MAIN() == 0

    docs_index = output / "docs" / "index.html"
    rendered = docs_index.read_text(encoding="utf-8")
    assert 'href="methodology.html#scope"' in rendered
    assert 'href="../references/frontier/VERIFICATION.html"' in rendered
    assert 'href="https://example.com/source.md#published"' in rendered
    assert 'href="#local-section"' in rendered
    for href in re.findall(r'href="([^"#]+\.html)(?:#[^"]*)?"', rendered):
        assert (docs_index.parent / href).resolve().is_file()


def test_inline_does_not_rewrite_root_absolute_markdown_link() -> None:
    rendered = INLINE("[Root document](/docs/root.md)")

    assert rendered == '<a href="/docs/root.md">Root document</a>'
