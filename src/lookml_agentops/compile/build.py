"""Compile every spoke into ``build/`` and write ``build/manifest.json``."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from lookml_agentops._util.gitinfo import project_revision
from lookml_agentops._util.hashing import canonical_json, sha256_file, sha256_text
from lookml_agentops._util.io import dump_yaml, write_text
from lookml_agentops.catalog import load_catalog
from lookml_agentops.compile.exporters.ca_exporter import export_ca
from lookml_agentops.compile.exporters.json_exporter import export_json
from lookml_agentops.compile.generate import compile_agent, finalize
from lookml_agentops.compile.schema import AgentInstructions
from lookml_agentops.compile.testgen import generate_tests
from lookml_agentops.config import LkagentConfig
from lookml_agentops.lookml.resolve import EffectiveModel, load_workspace, resolve_project
from lookml_agentops.verify.models import TestCase, TestFile

BUILD_SCHEMA = "lkagent.build.v1"


@dataclass
class BuildResult:
    out_dir: Path
    agents: dict[str, AgentInstructions]
    files: list[Path]
    warnings: list[str] = field(default_factory=list)


@dataclass
class CompiledAgent:
    instructions: AgentInstructions
    tests: list[TestCase]
    model: EffectiveModel


def compile_agents(cfg: LkagentConfig) -> dict[str, CompiledAgent]:
    """Compile every spoke in memory (used by ``compile`` and ``verify``)."""
    ws = load_workspace(cfg)
    catalog = load_catalog(cfg)
    hub_model = resolve_project(ws, cfg.hub)
    out: dict[str, CompiledAgent] = {}
    for spoke in cfg.spokes:
        em = resolve_project(ws, spoke)
        instr = compile_agent(hub=cfg.hub, em=em, hub_model=hub_model, catalog=catalog)
        tests = generate_tests(instr, em)
        finalize(instr)
        out[spoke] = CompiledAgent(instr, tests, em)
    return out


def compile_all(cfg: LkagentConfig, out_dir: Path | None = None) -> BuildResult:
    out = out_dir or cfg.path(cfg.compile.out_dir)
    agents: dict[str, AgentInstructions] = {}
    files: list[Path] = []
    warnings: list[str] = []
    manifest_agents: dict[str, object] = {}
    for spoke, ca in compile_agents(cfg).items():
        instr, tests = ca.instructions, ca.tests
        agents[spoke] = instr
        ca_json, ca_warn = export_ca(instr)
        warnings += ca_warn
        payloads = {
            "agent_instructions.json": export_json(instr),
            "ca_agent.json": ca_json,
            "tests.generated.yaml": dump_yaml(
                TestFile(tests=tests).model_dump(mode="json", exclude_defaults=True)
            ),
        }
        for name, text in payloads.items():
            path = out / spoke / name
            write_text(path, text)
            files.append(path)
        manifest_agents[spoke] = {
            "content_hash": instr.content_hash,
            "layers": instr.layer_hashes(),
            "rules": len(instr.all_rules()),
            "tests": len(tests),
            "files": {n: sha256_text(t) for n, t in sorted(payloads.items())},
        }
    manifest = {
        "schema": BUILD_SCHEMA,
        "agents": manifest_agents,
        "inputs": {
            "projects": {
                p: project_revision(p, cfg.project_path(p)).tree_hash for p in sorted(cfg.projects)
            },
            "catalog": sha256_file(cfg.path(cfg.catalog.path))
            if cfg.catalog.adapter == "yaml"
            else None,
        },
    }
    mpath = out / "manifest.json"
    write_text(mpath, canonical_json(manifest))
    files.append(mpath)
    return BuildResult(out_dir=out, agents=agents, files=files, warnings=warnings)


def check_build(cfg: LkagentConfig) -> list[str]:
    """Return the relative paths of build artifacts that are missing or stale."""
    committed = cfg.path(cfg.compile.out_dir)
    with tempfile.TemporaryDirectory() as tmp:
        fresh = compile_all(cfg, Path(tmp))
        stale: list[str] = []
        fresh_rel = {p.relative_to(fresh.out_dir).as_posix() for p in fresh.files}
        for rel in sorted(fresh_rel):
            a, b = Path(tmp) / rel, committed / rel
            if not b.exists() or a.read_bytes() != b.read_bytes():
                stale.append(rel)
        if committed.exists():
            for p in sorted(committed.rglob("*")):
                rel = p.relative_to(committed).as_posix()
                if p.is_file() and rel not in fresh_rel:
                    stale.append(f"{rel} (unexpected)")
        return stale
