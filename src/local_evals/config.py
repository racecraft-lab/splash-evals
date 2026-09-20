"""Configuration loading and external-state path qualification."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import yaml

from .models import EvaluationConfig


class ConfigurationError(ValueError):
    """Raised when configuration or path safety checks fail."""


def _lexical_absolute(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return expanded.absolute()


def reject_symlink_components(path: Path) -> None:
    """Reject every existing symlink component before resolving ``path``.

    Checking before ``Path.resolve`` avoids turning a symlink escape into an
    apparently ordinary canonical path. Missing tail components are allowed so
    callers can qualify a location before creating it.
    """

    candidate = _lexical_absolute(path)
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ConfigurationError("path contains a symbolic-link component")


def secure_resolve(path: str | os.PathLike[str], *, must_exist: bool = False) -> Path:
    candidate = Path(path)
    reject_symlink_components(candidate)
    try:
        return candidate.expanduser().resolve(strict=must_exist)
    except (OSError, RuntimeError) as exc:
        raise ConfigurationError("path cannot be resolved safely") from exc


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


class ExternalStatePaths:
    """Resolved public-source and private-state locations.

    The object stores paths for local use but its representation intentionally
    omits them so accidental diagnostics do not disclose workstation paths.
    """

    __slots__ = ("project_root", "state_dir")

    def __init__(self, project_root: Path, state_dir: Path) -> None:
        self.project_root = project_root
        self.state_dir = state_dir

    def __repr__(self) -> str:
        return "ExternalStatePaths(project_root=<private>, state_dir=<private>)"

    @classmethod
    def resolve(
        cls,
        *,
        project_root: str | os.PathLike[str] | None = None,
        state_dir: str | os.PathLike[str] | None = None,
        require_project: bool = True,
        require_state: bool = False,
    ) -> ExternalStatePaths:
        raw_project = project_root or os.environ.get("PROJECT_ROOT") or Path.cwd()
        state_home = os.environ.get("XDG_STATE_HOME", "~/.local/state")
        default_state = Path(state_home) / "splash-evals"
        raw_state = state_dir or os.environ.get("LOCAL_EVALS_STATE_DIR") or default_state
        resolved_project = secure_resolve(raw_project, must_exist=require_project)
        resolved_state = secure_resolve(raw_state, must_exist=require_state)

        if resolved_project == resolved_state or _is_within(resolved_state, resolved_project):
            raise ConfigurationError("private state directory must be outside the source worktree")
        if resolved_project.exists() and not resolved_project.is_dir():
            raise ConfigurationError("project root is not a directory")
        if resolved_state.exists():
            if not resolved_state.is_dir():
                raise ConfigurationError("private state path is not a directory")
            owner = resolved_state.stat().st_uid
            if hasattr(os, "getuid") and owner != os.getuid():
                raise ConfigurationError("private state directory is not owned by this user")
            if resolved_state.stat().st_mode & 0o077:
                raise ConfigurationError(
                    "private state directory permissions must be 0700 or stricter"
                )
        return cls(resolved_project, resolved_state)


def _safe_config_path(project_root: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise ConfigurationError("configuration path must stay inside the project")
    candidate = secure_resolve(project_root / relative, must_exist=True)
    if not _is_within(candidate, project_root):
        raise ConfigurationError("configuration path escaped the project root")
    if not candidate.is_file():
        raise ConfigurationError("configuration path is not a file")
    return candidate


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigurationError("configuration document must be a mapping")
    return data


def load_profile(
    name: str, *, project_root: str | os.PathLike[str] | None = None
) -> EvaluationConfig:
    if not name or Path(name).name != name:
        raise ConfigurationError("profile name must be a single safe path component")
    paths = ExternalStatePaths.resolve(project_root=project_root)
    path = _safe_config_path(paths.project_root, Path("configs") / "profiles" / f"{name}.yaml")
    return EvaluationConfig.model_validate(load_yaml(path))


def load_suite(name: str, *, project_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    if not name or Path(name).name != name:
        raise ConfigurationError("suite name must be a single safe path component")
    paths = ExternalStatePaths.resolve(project_root=project_root)
    path = _safe_config_path(paths.project_root, Path("configs") / "profiles" / f"{name}.yaml")
    return load_yaml(path)
