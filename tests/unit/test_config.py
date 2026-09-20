from __future__ import annotations

import os
from pathlib import Path

import pytest

from local_evals.config import ConfigurationError, ExternalStatePaths, secure_resolve


def test_external_state_must_be_outside_source_tree(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = project / "private-state"
    project.mkdir()

    with pytest.raises(ConfigurationError, match="outside"):
        ExternalStatePaths.resolve(project_root=project, state_dir=state)


def test_external_state_rejects_group_or_world_permissions(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "private-state"
    project.mkdir()
    state.mkdir(mode=0o700)
    state.chmod(0o755)

    with pytest.raises(ConfigurationError, match="0700"):
        ExternalStatePaths.resolve(project_root=project, state_dir=state)


def test_external_state_accepts_restrictive_permissions(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = tmp_path / "private-state"
    project.mkdir()
    state.mkdir(mode=0o700)

    paths = ExternalStatePaths.resolve(project_root=project, state_dir=state)

    assert paths.project_root == project.resolve()
    assert paths.state_dir == state.resolve()
    assert str(project) not in repr(paths)
    assert str(state) not in repr(paths)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_secure_resolve_rejects_symlink_component(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ConfigurationError, match="symbolic-link"):
        secure_resolve(link / "future.json")
