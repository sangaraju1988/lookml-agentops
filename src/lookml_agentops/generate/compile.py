"""``lkagent generate compile``: ``*.agent.md`` -> agent_spec.v1 -> exporters + generated tests.

Deterministic: same inputs give byte-identical build output (no timestamps, sorted keys).
Build layout::

    build/<agent>/agent_spec.json        neutral schema with provenance
    build/<agent>/ca_context.json        CA API authored context (staging/published context value)
    build/<agent>/looker_ui.md           text for the Looker UI agent editor
    build/<agent>/tests.generated.yaml   one test per rule / vocabulary / guardrail / golden query
    build/manifest.json                  hashes of everything above
    build/golden_cache.json              Explore URL -> Looker query (from resolve-golden; optional)

Abstract specs (no explores, e.g. a shared baseline) are validated but not built.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from lookml_agentops._util.hashing import canonical_json, sha256_text
from lookml_agentops._util.io import dump_yaml, write_text
from lookml_agentops.catalog import load_catalog
from lookml_agentops.config import LkagentConfig
from lookml_agentops.diagnose.models import TestCase, TestFile
from lookml_agentops.generate.bind import (
    Binder,
    Binding,
    SpecFinding,
    apply_golden_cache,
    load_golden_cache,
)
from lookml_agentops.generate.exporters.ca_api import export_ca_context
from lookml_agentops.generate.exporters.json_exporter import export_json
from lookml_agentops.generate.exporters.looker_ui import export_looker_ui
from lookml_agentops.generate.testgen import generate_tests
from lookml_agentops.lookml.resolve import EffectiveModel, load_workspace, resolve_project
from lookml_agentops.spec.discover import discover_specs
from lookml_agentops.spec.errors import SpecError
from lookml_agentops.spec.model import AgentSpec
from lookml_agentops.spec.resolve import resolve_spec

BUILD_SCHEMA = "lkagent.build.v2"
GOLDEN_CACHE = "golden_cache.json"


class CompileError(Exception):
    def __init__(self, findings: list[SpecFinding]) -> None:
        self.findings = findings
        super().__init__("\n".join(f"{f.file}:{f.line}: {f.rule_id} {f.message}" for f in findings))


@dataclass
class CompiledAgent:
    spec: AgentSpec
    binding: Binding
    tests: list[TestCase]

    @property
    def models(self) -> dict[str, EffectiveModel]:
        return {be.project: be.model for be in self.binding.explores}


@dataclass
class BindAll:
    bindings: dict[str, Binding] = field(default_factory=dict)
    findings: list[SpecFinding] = field(default_factory=list)  # parse/resolve + binding findings
    models: dict[str, EffectiveModel] = field(default_factory=dict)


def load_models(cfg: LkagentConfig) -> dict[str, EffectiveModel]:
    ws = load_workspace(cfg)
    return {p: resolve_project(ws, p) for p in sorted(cfg.projects) if ws.projects[p].model_files}


def bind_all(cfg: LkagentConfig, models: dict[str, EffectiveModel] | None = None) -> BindAll:
    out = BindAll(models=models if models is not None else load_models(cfg))
    binder = Binder(out.models, load_catalog(cfg))
    cache = load_golden_cache(cfg.path(cfg.build.out_dir) / GOLDEN_CACHE)
    try:
        files = discover_specs(cfg)
    except SpecError as exc:
        out.findings.append(
            SpecFinding(exc.rule_id, "error", exc.message, exc.file, exc.line, exc.file)
        )
        return out
    for sf in files:
        try:
            spec = resolve_spec(sf.path, cfg.root)
        except SpecError as exc:
            out.findings.append(
                SpecFinding(exc.rule_id, "error", exc.message, exc.file, exc.line, sf.agent_id)
            )
            continue
        apply_golden_cache(spec, cache)
        b = binder.bind(spec)
        out.bindings[spec.agent_id] = b
        out.findings.extend(b.findings)
    # the same inherited element is reported once
    out.findings = sorted(set(out.findings), key=lambda f: (f.file, f.line, f.rule_id, f.message))
    return out


def compile_agents(
    cfg: LkagentConfig,
    agent: str | None = None,
    *,
    strict: bool = True,
    models: dict[str, EffectiveModel] | None = None,
) -> dict[str, CompiledAgent]:
    ba = bind_all(cfg, models)
    wanted = [
        a
        for a, b in sorted(ba.bindings.items())
        if not b.spec.is_abstract and (agent is None or a == agent)
    ]
    if agent is not None and agent not in ba.bindings:
        errs = [f for f in ba.findings if f.severity == "error"] or [
            SpecFinding(
                "LKS011", "error", f"no agent spec with id {agent!r}", str(cfg.source), 1, agent
            )
        ]
        raise CompileError(errs)
    if strict:
        wanted_files = {ba.bindings[a].spec.source.file for a in wanted} | {
            c.file for a in wanted for c in ba.bindings[a].spec.extends_chain
        }
        errors = [
            f
            for f in ba.findings
            if f.severity == "error" and (agent is None or f.file in wanted_files)
        ]
        if errors:
            raise CompileError(errors)
    out: dict[str, CompiledAgent] = {}
    for a in wanted:
        b = ba.bindings[a]
        tests = generate_tests(b)
        out[a] = CompiledAgent(b.spec.finalize(), b, tests)
    return out


@dataclass
class BuildResult:
    out_dir: Path
    agents: dict[str, CompiledAgent]
    files: list[Path]
    warnings: list[str] = field(default_factory=list)


def write_build(
    cfg: LkagentConfig, out_dir: Path | None = None, agent: str | None = None
) -> BuildResult:
    out = out_dir or cfg.path(cfg.build.out_dir)
    agents = compile_agents(cfg, agent)
    files: list[Path] = []
    warnings: list[str] = []
    manifest: dict[str, object] = {}
    for aid, ca in agents.items():
        ctx_json, w = export_ca_context(ca.spec)
        warnings += w
        payloads = {
            "agent_spec.json": export_json(ca.spec),
            "ca_context.json": ctx_json,
            "looker_ui.md": export_looker_ui(ca.spec),
            "tests.generated.yaml": dump_yaml(
                TestFile(tests=ca.tests).model_dump(mode="json", exclude_defaults=True)
            ),
        }
        for name, text in payloads.items():
            p = out / aid / name
            write_text(p, text)
            files.append(p)
        manifest[aid] = {
            "content_hash": ca.spec.content_hash,
            "extends": [c.agent_id for c in ca.spec.extends_chain],
            "rules": len(ca.spec.active_rules()),
            "tests": len(ca.tests),
            "files": {n: sha256_text(t) for n, t in sorted(payloads.items())},
        }
    if agent is None:
        mpath = out / "manifest.json"
        write_text(mpath, canonical_json({"schema": BUILD_SCHEMA, "agents": manifest}))
        files.append(mpath)
    return BuildResult(out, agents, files, warnings)


def check_build(cfg: LkagentConfig) -> list[str]:
    committed = cfg.path(cfg.build.out_dir)
    with tempfile.TemporaryDirectory() as tmp:
        fresh = write_build(cfg, Path(tmp))
        fresh_rel = {p.relative_to(fresh.out_dir).as_posix() for p in fresh.files}
        stale = [
            rel
            for rel in sorted(fresh_rel)
            if not (committed / rel).exists()
            or (committed / rel).read_bytes() != (Path(tmp) / rel).read_bytes()
        ]
        if committed.exists():
            for p in sorted(committed.rglob("*")):
                rel = p.relative_to(committed).as_posix()
                if p.is_file() and rel not in fresh_rel and rel != GOLDEN_CACHE:
                    stale.append(f"{rel} (unexpected)")
        return stale
