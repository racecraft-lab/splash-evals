from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from runpy import run_path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS_ARTIFACT = run_path(str(ROOT / "scripts" / "docs_artifact.py"))
CHECKED_PATH = cast(Callable[[Path, str | Path], Path], DOCS_ARTIFACT["checked_artifact_path"])
PREPARE = cast(Callable[[Path, str | Path], Path], DOCS_ARTIFACT["prepare_artifact"])
VERIFY = cast(Callable[[Path, str | Path], Path], DOCS_ARTIFACT["verify_artifact"])
ARTIFACT_ERROR = cast(type[Exception], DOCS_ARTIFACT["ArtifactError"])


def test_artifact_path_is_fixed_inside_repository(tmp_path: Path) -> None:
    assert CHECKED_PATH(tmp_path, "docs-site/dist") == tmp_path / "docs-site" / "dist"
    for unsafe in (".", "_site", "docs-site/../outside", tmp_path.parent / "outside"):
        with pytest.raises(ARTIFACT_ERROR):
            CHECKED_PATH(tmp_path, unsafe)


def test_prepare_removes_only_stale_docs_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "docs-site" / "dist"
    artifact.mkdir(parents=True)
    (artifact / "stale.html").write_text("stale", encoding="utf-8")
    neighbor = tmp_path / "docs-site" / "keep.txt"
    neighbor.write_text("keep", encoding="utf-8")

    assert PREPARE(tmp_path, "docs-site/dist") == artifact
    assert not artifact.exists()
    assert neighbor.read_text(encoding="utf-8") == "keep"


def test_verify_requires_nonempty_regular_files(tmp_path: Path) -> None:
    artifact = tmp_path / "docs-site" / "dist"
    artifact.mkdir(parents=True)
    with pytest.raises(ARTIFACT_ERROR, match="empty"):
        VERIFY(tmp_path, "docs-site/dist")

    (artifact / "index.html").write_text("ok", encoding="utf-8")
    assert VERIFY(tmp_path, "docs-site/dist") == artifact
