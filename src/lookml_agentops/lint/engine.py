"""Lint engine: builds the context, runs rules, applies config and exemptions."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal

from lookml_agentops.catalog import Catalog, load_catalog
from lookml_agentops.config import LkagentConfig
from lookml_agentops.lookml.model import CATALOG_PROJECT, Loc
from lookml_agentops.lookml.resolve import (
    EffectiveModel,
    Workspace,
    load_workspace,
    resolve_project,
)

Severity = Literal["error", "warning", "note"]
SEVERITY_ORDER = {"error": 0, "warning": 1, "note": 2}


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    message: str
    object: str
    loc: Loc | None

    def sort_key(self) -> tuple[str, str, int, str, str]:
        loc = self.loc
        return (
            loc.project if loc else "",
            loc.file if loc else "",
            loc.line if loc else 0,
            self.rule_id,
            self.object,
        )


@dataclass(frozen=True)
class RawFinding:
    message: str
    object: str
    loc: Loc | None
    exemptions: frozenset[str] = frozenset()


@dataclass
class LintContext:
    cfg: LkagentConfig
    ws: Workspace
    models: dict[str, EffectiveModel]
    catalog: Catalog
    catalog_rel: str

    @property
    def hub(self) -> str:
        return self.cfg.hub

    def catalog_loc(self, term_id: str) -> Loc:
        return Loc(CATALOG_PROJECT, self.catalog_rel, self.catalog.lines.get(term_id, 1))


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    severity: Severity
    rationale: str
    fix_hint: str
    check: Callable[[LintContext], Iterable[RawFinding]] = field(compare=False)


@dataclass
class LintResult:
    findings: list[Finding]
    rules: list[Rule]

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")


def build_context(cfg: LkagentConfig) -> LintContext:
    ws = load_workspace(cfg)
    models = {p: resolve_project(ws, p) for p in sorted(cfg.projects)}
    catalog = load_catalog(cfg)
    try:
        rel = cfg.path(cfg.catalog.path).resolve().relative_to(cfg.root).as_posix()
    except ValueError:
        rel = cfg.catalog.path
    return LintContext(cfg=cfg, ws=ws, models=models, catalog=catalog, catalog_rel=rel)


def run_lint(cfg: LkagentConfig, ctx: LintContext | None = None) -> LintResult:
    from lookml_agentops.lint.rules import ALL_RULES

    ctx = ctx or build_context(cfg)
    findings: set[Finding] = set()
    active: list[Rule] = []
    for rule in ALL_RULES:
        override = cfg.lint.rules.get(rule.id)
        if override is not None and not override.enabled:
            continue
        severity: Severity = rule.severity
        if override is not None and override.severity is not None:
            severity = override.severity
        active.append(rule)
        for raw in rule.check(ctx):
            if rule.id in raw.exemptions:
                continue
            findings.add(Finding(rule.id, severity, raw.message, raw.object, raw.loc))
    ordered = sorted(findings, key=Finding.sort_key)
    return LintResult(findings=ordered, rules=active)
