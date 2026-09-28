"""Versions for tracked inputs.

An input's version is the SHA of the last git commit that touched its path, plus a ``+dirty``
content-hash suffix when the working tree differs. Outside git it is a content hash. Using the
last commit *for that path* (not HEAD) keeps a project's version stable across unrelated commits
in a monorepo.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from lookml_agentops._util.hashing import sha256_file, tree_hash


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def content_hash(path: Path) -> str:
    return tree_hash(path) if path.is_dir() else sha256_file(path)


def input_version(path: Path) -> str:
    path = path.resolve()
    cwd = path if path.is_dir() else path.parent
    sha = _git(["log", "-1", "--format=%H", "--", str(path)], cwd)
    if not sha:
        return f"sha256:{content_hash(path)[:16]}"
    dirty = _git(["status", "--porcelain", "--", str(path)], cwd)
    return f"{sha}+dirty.{content_hash(path)[:12]}" if dirty else sha
