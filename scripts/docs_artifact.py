#!/usr/bin/env python3
"""Safely prepare and verify the generated documentation artifact."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
from collections.abc import Sequence
from pathlib import Path


class ArtifactError(Exception):
    """An actionable documentation artifact validation failure."""


CANONICAL_ARTIFACT_PATH = Path("docs-site/dist")


def checked_artifact_path(repo_root: Path, supplied_path: str | Path) -> Path:
    """Return the fixed in-repository artifact path when it is safe to inspect or remove."""
    root = repo_root.resolve(strict=True)
    raw = Path(supplied_path)
    if ".." in raw.parts:
        raise ArtifactError("artifact path must not contain '..'")
    candidate = raw if raw.is_absolute() else root / raw
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ArtifactError("artifact path must stay inside the repository") from exc
    if relative != CANONICAL_ARTIFACT_PATH:
        raise ArtifactError(f"only '{CANONICAL_ARTIFACT_PATH}' is an allowed artifact path")

    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ArtifactError(f"artifact path traverses a symlink: {current}")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ArtifactError("artifact path resolves outside the repository")
    return resolved


def prepare_artifact(repo_root: Path, supplied_path: str | Path) -> Path:
    """Remove only the fixed artifact directory so stale output cannot be uploaded."""
    artifact = checked_artifact_path(repo_root, supplied_path)
    if artifact.exists():
        if not artifact.is_dir():
            raise ArtifactError("artifact path exists but is not a directory")
        shutil.rmtree(artifact)
    return artifact


def _verify_entry(entry: Path, artifact: Path, *, expected_directory: bool) -> None:
    """Reject links, special files, and hard-linked files in a Pages artifact."""
    metadata = entry.lstat()
    relative = entry.relative_to(artifact)
    if stat.S_ISLNK(metadata.st_mode):
        raise ArtifactError(f"artifact contains a symbolic link: {relative}")
    if expected_directory:
        if not stat.S_ISDIR(metadata.st_mode):
            raise ArtifactError(f"invalid directory entry: {relative}")
        return
    if not stat.S_ISREG(metadata.st_mode):
        raise ArtifactError(f"artifact contains a special file: {relative}")
    if metadata.st_nlink != 1:
        raise ArtifactError(f"artifact contains a hard-linked file: {relative}")


def verify_artifact(repo_root: Path, supplied_path: str | Path) -> Path:
    """Require a non-empty artifact tree containing only standalone regular files."""
    artifact = checked_artifact_path(repo_root, supplied_path)
    if not artifact.is_dir():
        raise ArtifactError("documentation artifact is missing")
    has_content = False
    for directory, child_directories, files in os.walk(artifact, followlinks=False):
        directory_path = Path(directory)
        for name, expected_directory in (
            *((name, True) for name in child_directories),
            *((name, False) for name in files),
        ):
            has_content = True
            _verify_entry(
                directory_path / name,
                artifact,
                expected_directory=expected_directory,
            )
    if not has_content:
        raise ArtifactError("documentation artifact is empty")
    return artifact


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "verify"))
    parser.add_argument("artifact_path")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if args.operation == "prepare":
        prepare_artifact(root, args.artifact_path)
    else:
        verify_artifact(root, args.artifact_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
