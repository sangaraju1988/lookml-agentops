from __future__ import annotations

import datetime as dt
from pathlib import Path

from lookml_agentops.config import LkagentConfig, ProjectConfig


def write_projects(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def mini_config(root: Path, projects: dict[str, str]) -> LkagentConfig:
    cfg = LkagentConfig(
        name="mini",
        as_of=dt.date(2026, 1, 20),
        projects={n: ProjectConfig(path=n, role=r) for n, r in projects.items()},  # type: ignore[arg-type]
    )
    cfg.root = root
    return cfg
