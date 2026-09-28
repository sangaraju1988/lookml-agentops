"""``lkagent.yaml`` (version 2) project configuration.

The config declares *inputs* — LookML projects, agent specs, catalogs, test suites — without any
built-in topology. Projects may import zero or many other projects; the resolver handles any
import DAG. Paths are relative to the config file.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lookml_agentops._util.io import load_yaml

CONFIG_FILENAME = "lkagent.yaml"
CONFIG_VERSION = 2


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


class AgentsConfig(_Strict):
    paths: list[str] = Field(default_factory=lambda: ["agents/**/*.agent.md"])


class CatalogConfig(_Strict):
    adapter: Literal["yaml", "dataplex"] = "yaml"
    path: str = "catalog/glossary.yaml"


class RuleOverride(_Strict):
    enabled: bool = True
    severity: Literal["error", "warning", "note"] | None = None


class LintConfig(_Strict):
    max_ai_fields: int = 150
    rules: dict[str, RuleOverride] = Field(default_factory=dict)


class BuildConfig(_Strict):
    out_dir: str = "build"


class LookerConfig(_Strict):
    """Looker API access for `generate resolve-golden`. Secrets come from env vars only."""

    base_url_env: str = "LOOKER_BASE_URL"
    api_version: str = "4.0"


class CARunnerConfig(_Strict):
    """Non-secret CA settings. Secrets come from env vars only (see docs/own-looker.md)."""

    endpoint: str = "https://geminidataanalytics.googleapis.com/v1"
    location: str = "global"
    context_version: Literal["STAGING", "PUBLISHED"] = "PUBLISHED"
    agents: dict[str, str] = Field(
        default_factory=dict
    )  # agent id -> data agent id or resource name
    timeout_seconds: float = 120.0


class MCPRunnerConfig(_Strict):
    """Non-secret MCP settings. URL and bearer token come from env vars."""

    tool: str = ""  # TODO(verify-api): tool name exposed by your MCP server
    question_arg: str = "question"
    extra_args: dict[str, str] = Field(default_factory=dict)
    agent_args: dict[str, dict[str, str]] = Field(default_factory=dict)
    timeout_seconds: float = 120.0


class DeployConfig(_Strict):
    pass_threshold: float = 1.0  # minimum pass rate against staging before publishing


class DiagnoseConfig(_Strict):
    runner: Literal["mock", "ca", "mcp"] = "mock"
    scenario: str = "baseline"
    history: str = ".lkagent/history.duckdb"
    ca: CARunnerConfig = Field(default_factory=CARunnerConfig)
    mcp: MCPRunnerConfig = Field(default_factory=MCPRunnerConfig)
    deploy: DeployConfig = Field(default_factory=DeployConfig)


class LkagentConfig(_Strict):
    version: Literal[2] = 2
    name: str
    as_of: dt.date
    seed: SeedConfig = Field(default_factory=SeedConfig)
    projects: dict[str, ProjectConfig] = Field(default_factory=dict)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    catalogs: dict[str, CatalogConfig] = Field(default_factory=dict)
    suites: dict[str, str] = Field(default_factory=dict)  # suite name -> yaml path
    owners: str | None = "owners.yaml"
    lint: LintConfig = Field(default_factory=LintConfig)
    build: BuildConfig = Field(default_factory=BuildConfig)
    looker: LookerConfig = Field(default_factory=LookerConfig)
    diagnose: DiagnoseConfig = Field(default_factory=DiagnoseConfig)

    # Set by :func:`load_config`; not part of the file.
    root: Path = Field(default=Path("."), exclude=True)
    source: Path | None = Field(default=None, exclude=True)

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (self.root / p)

    def project_path(self, name: str) -> Path:
        return self.path(self.projects[name].path)

    def catalog(self) -> tuple[str, CatalogConfig] | None:
        """The (single) glossary catalog used for lint/derive, if any."""
        if not self.catalogs:
            return None
        name = sorted(self.catalogs)[0]
        return name, self.catalogs[name]


class ConfigError(Exception):
    pass


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
    if data.get("version") != CONFIG_VERSION:
        raise ConfigError(
            f"{cfg_path}: expected `version: {CONFIG_VERSION}` (got {data.get('version')!r}). "
            "See docs/generalization-plan.md for the v1 -> v2 changes."
        )
    cfg = LkagentConfig.model_validate(data)
    cfg.root = cfg_path.parent.resolve()
    cfg.source = cfg_path.resolve()
    return cfg
