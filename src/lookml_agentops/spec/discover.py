"""Find agent specs declared in lkagent.yaml (``agents.paths`` globs)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lookml_agentops.config import LkagentConfig
from lookml_agentops.spec.errors import SpecError
from lookml_agentops.spec.parse import parse_file


@dataclass(frozen=True)
class SpecFile:
    agent_id: str
    path: Path
    rel: str


def discover_specs(cfg: LkagentConfig) -> list[SpecFile]:
    found: dict[str, SpecFile] = {}
    for pattern in cfg.agents.paths:
        for path in sorted(cfg.root.glob(pattern)):
            parsed = parse_file(path, cfg.root)
            aid = parsed.front.agent
            if aid in found and found[aid].path != path:
                raise SpecError(
                    f"agent id {aid!r} is also defined in {found[aid].rel}", file=parsed.rel
                )
            found[aid] = SpecFile(aid, path, parsed.rel)
    return [found[k] for k in sorted(found)]
