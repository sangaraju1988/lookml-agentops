"""Revision identity for Looker projects.

A project's *tree hash* (content hash of its files) is what attribution compares: in a monorepo
the commit SHA changes for every change anywhere, so it cannot tell hub from spoke. The commit SHA
is still recorded for traceability when the project lives in a git checkout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel

from lookml_agentops._util.hashing import tree_hash


class ProjectRevision(BaseModel):
    project: str
    tree_hash: str
    commit_sha: str | None = None


def git_commit_sha(path: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha or None


def project_revision(name: str, path: Path) -> ProjectRevision:
    return ProjectRevision(
        project=name,
        tree_hash=tree_hash(path, suffixes=(".lkml", ".lookml", ".yaml", ".yml")),
        commit_sha=git_commit_sha(path),
    )
