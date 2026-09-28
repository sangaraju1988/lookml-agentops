"""PR modes: check out one project at a branch in a git worktree, keep the others pinned."""

from __future__ import annotations

import contextlib
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

from lookml_agentops.config import LkagentConfig


class ModeError(Exception):
    pass


def _git(*args: str, cwd: Path) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=60
        )
    except subprocess.CalledProcessError as exc:
        raise ModeError(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc
    return out.stdout.strip()


@contextlib.contextmanager
def project_at_branch(cfg: LkagentConfig, project: str, branch: str) -> Iterator[LkagentConfig]:
    """Yield a config whose ``project`` path points at a worktree of ``branch``."""
    path = cfg.project_path(project).resolve()
    top = Path(_git("rev-parse", "--show-toplevel", cwd=path)).resolve()
    rel = path.relative_to(top)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{project}-{branch}")
    wt = (cfg.root / ".lkagent" / "worktrees" / safe).resolve()
    if wt.exists():
        _git("worktree", "remove", "--force", str(wt), cwd=top)
    _git("worktree", "add", "--detach", str(wt), branch, cwd=top)
    try:
        new = cfg.model_copy(deep=True)
        new.root = cfg.root
        new.projects[project].path = str(wt / rel)
        yield new
    finally:
        with contextlib.suppress(ModeError):
            _git("worktree", "remove", "--force", str(wt), cwd=top)
