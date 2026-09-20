#!/usr/bin/env python3
"""Fail closed when distribution archives contain paths outside the package allowlist."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

FORBIDDEN_PARTS = {
    ".env",
    ".git",
    "data",
    "datasets",
    "models",
    "outputs",
    "runs",
    "snapshots",
    "work",
}
FORBIDDEN_SUFFIXES = {".gguf", ".safetensors", ".sqlite", ".db", ".log", ".ipynb", ".dmg"}


def check_member(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("unsafe archive path category: traversal")
    if any(part in FORBIDDEN_PARTS or part.startswith(".env") for part in path.parts):
        raise ValueError("unsafe archive path category: private-state")
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        raise ValueError("unsafe archive path category: forbidden-type")


def inspect(path: Path) -> None:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                check_member(member.filename)
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                check_member(member.name)
                if member.issym() or member.islnk():
                    raise ValueError("unsafe archive member category: link")
    else:
        raise ValueError(f"unapproved release artifact type: {path.name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    artifacts = sorted((*args.directory.glob("*.whl"), *args.directory.glob("*.tar.gz")))
    if not artifacts:
        raise SystemExit("no wheel or sdist found")
    for artifact in artifacts:
        inspect(artifact)
    print(f"inspected {len(artifacts)} release archive(s); no forbidden member categories found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
