"""``lkagent.yaml`` project configuration."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lookml_agentops._util.io import load_yaml

CONFIG_FILENAME = "lkagent.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SeedConfig(_Strict):
    seed: int = 20260120
    scale: float = 1.0
    out_dir: str = "seed/data"
    duckdb: str = "seed/harborline.duckdb"
    manifest: str = "seed/manifest.json"


class ProjectConfig(_Strict):
    path: str
    role: Literal["hub", "spoke"]
    owner: str = ""


class CatalogConfig(_Strict):
    adapter: Literal["yaml", "dataplex"] = "yaml"
    path: str = "catalog/glossary.yaml"


class RuleOverride(_Strict):
    enabled: bool = True
    severity: Literal["error", "warning", "note"] | None = None


class LintConfig(_Strict):
    max_ai_fields: int = 150
    rules: dict[str, RuleOverride] = Field(default_factory=dict)


class CompileConfig(_Strict):
    out_dir: str = "build"


class CARunnerConfig(_Strict):
    """Non-secret CA settings. Secrets come from env vars only (see docs/own-looker.md)."""

    endpoint: str = "https://geminidataanalytics.googleapis.com/v1"
    location: str = "global"
    context_version: Literal["STAGING", "PUBLISHED"] = "PUBLISHED"
    agents: dict[str, str] = Field(default_factory=dict)  # spoke -> data agent id or resource name
    timeout_seconds: float = 120.0


class MCPRunnerConfig(_Strict):
    """Non-secret MCP settings. URL and bearer token come from env vars."""

    tool: str = ""  # TODO(verify-api): tool name exposed by your MCP server
    question_arg: str = "question"
    extra_args: dict[str, str] = Field(default_factory=dict)
    spoke_args: dict[str, dict[str, str]] = Field(default_factory=dict)
    timeout_seconds: float = 120.0


class VerifyConfig(_Strict):
    runner: Literal["mock", "ca", "mcp"] = "mock"
    vendor_profile: str = "v1"
    history: str = ".lkagent/history.duckdb"
    ca: CARunnerConfig = Field(default_factory=CARunnerConfig)
    mcp: MCPRunnerConfig = Field(default_factory=MCPRunnerConfig)


class LkagentConfig(_Strict):
    version: int = 1
    name: str
    as_of: dt.date
    seed: SeedConfig = Field(default_factory=SeedConfig)
    projects: dict[str, ProjectConfig]
    catalog: CatalogConfig = Field(default_factory=CatalogConfig)
    lint: LintConfig = Field(default_factory=LintConfig)
    compile: CompileConfig = Field(default_factory=CompileConfig)
    golden: list[str] = Field(default_factory=list)
    verify: VerifyConfig = Field(default_factory=VerifyConfig)

    # Set by :func:`load_config`; not part of the file.
    root: Path = Field(default=Path("."), exclude=True)

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (self.root / p)

    def project_path(self, name: str) -> Path:
        return self.path(self.projects[name].path)

    @property
    def hub(self) -> str:
        hubs = sorted(n for n, p in self.projects.items() if p.role == "hub")
        if len(hubs) != 1:
            raise ValueError(f"expected exactly one hub project, found {hubs}")
        return hubs[0]

    @property
    def spokes(self) -> list[str]:
        return sorted(n for n, p in self.projects.items() if p.role == "spoke")


def find_config(start: Path | None = None) -> Path:
    here = (start or Path.cwd()).resolve()
    if here.is_file():
        return here
    for d in (here, *here.parents):
        cand = d / CONFIG_FILENAME
        if cand.exists():
            return cand
    raise FileNotFoundError(f"no {CONFIG_FILENAME} found from {here}")


def load_config(path: Path | None = None) -> LkagentConfig:
    cfg_path = find_config(path)
    data = load_yaml(cfg_path) or {}
    cfg = LkagentConfig.model_validate(data)
    cfg.root = cfg_path.parent.resolve()
    return cfg
