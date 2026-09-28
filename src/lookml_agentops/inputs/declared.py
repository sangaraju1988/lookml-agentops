"""Inputs declared in lkagent.yaml (LookML projects, catalogs, suites, the config itself)."""

from __future__ import annotations

from lookml_agentops._util.gitinfo import input_version
from lookml_agentops.config import LkagentConfig
from lookml_agentops.inputs.model import TrackedInput, input_id
from lookml_agentops.inputs.owners import Owners


def load_owners(cfg: LkagentConfig) -> Owners:
    return Owners.load(cfg.path(cfg.owners) if cfg.owners else None)


def _rel(cfg: LkagentConfig, rel: str) -> str:
    try:
        return cfg.path(rel).resolve().relative_to(cfg.root).as_posix()
    except ValueError:
        return rel


def declared_inputs(cfg: LkagentConfig, owners: Owners | None = None) -> list[TrackedInput]:
    owners = owners or load_owners(cfg)
    out: list[TrackedInput] = []

    def add(kind, name: str, rel: str) -> None:  # type: ignore[no-untyped-def]
        iid = input_id(kind, name)
        out.append(
            TrackedInput(
                input_id=iid,
                kind=kind,
                version=input_version(cfg.path(rel)),
                owner=owners.owner(iid),
                path=_rel(cfg, rel),
            )
        )

    for name, p in sorted(cfg.projects.items()):
        add("lookml_project", name, p.path)
    for name, c in sorted(cfg.catalogs.items()):
        if c.adapter == "yaml":
            add("catalog", name, c.path)
    for name, path in sorted(cfg.suites.items()):
        add("test_suite", name, path)
    if cfg.source is not None:
        add("config", "lkagent", cfg.source.name)
    return out
