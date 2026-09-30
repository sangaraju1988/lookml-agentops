"""``lkagent init``: scaffold a lookml-agentops setup from existing LookML projects.

Writes, relative to the target directory:

* ``lkagent.yaml``                     — v2 config declaring every project found;
* ``owners.yaml``                      — one placeholder team per project (rename them);
* ``catalog/glossary.yaml``            — an empty glossary with a commented example;
* ``agents/<project>-analyst.agent.md`` — a starter spec per project with queryable explores
  (at most 5, the CA limit), derived context on, a guardrail for exposed PII-tagged fields and a
  starter golden query built from a real measure;
* ``suites/<agent>.yaml``              — a starter structural test per agent.

Projects without a model file (libraries that others import) are tracked, but get no agent.
Nothing is overwritten unless ``force`` is set. Output is deterministic for the same inputs.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lookml_agentops.config import LkagentConfig, ProjectConfig
from lookml_agentops.generate.bind import MAX_EXPLORES, BoundExplore, exposed_fields
from lookml_agentops.lookml.model import LExplore, LField
from lookml_agentops.lookml.resolve import EffectiveModel, load_workspace, resolve_project

SKIP_DIRS = {".git", ".venv", "node_modules", "build", ".lkagent", "__pycache__"}


class ScaffoldError(Exception):
    pass


@dataclass
class ScaffoldResult:
    root: Path
    projects: dict[str, Path]
    agents: dict[str, list[str]] = field(default_factory=dict)  # agent id -> project::explore
    libraries: list[str] = field(default_factory=list)  # projects without a model file
    written: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def project_name(path: Path) -> str:
    manifest = path / "manifest.lkml"
    if manifest.exists():
        m = re.search(r'project_name:\s*"([^"]+)"', manifest.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    return re.sub(r"[^A-Za-z0-9_]", "_", path.resolve().name)


def discover_projects(scan: Path, max_depth: int = 4) -> dict[str, Path]:
    """Directories under ``scan`` that look like LookML projects (manifest or model files)."""
    found: dict[str, Path] = {}
    scan = scan.resolve()
    for dirpath, dirnames, filenames in os.walk(scan):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        if len(here.relative_to(scan).parts) > max_depth:
            dirnames[:] = []
            continue
        if "manifest.lkml" in filenames or any(f.endswith(".model.lkml") for f in filenames):
            name = project_name(here)
            if name in found:
                raise ScaffoldError(
                    f"two projects are both named {name!r}: {found[name]} and {here}"
                )
            found[name] = here
            dirnames[:] = []  # a project's subfolders are part of it
    return dict(sorted(found.items()))


def agent_id_for(project: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", project.lower()).strip("-")
    slug = re.sub(r"-project$", "", slug) or "agent"
    return f"{slug}-analyst"


def _starter_measure(be: BoundExplore) -> tuple[str, LField] | None:
    view = be.model.views.get(be.explore.base_view)
    if view is None:
        return None
    measures = [f for f in view.fields.values() if f.kind == "measure" and not f.hidden]
    measures.sort(
        key=lambda f: (
            not f.has_tag("certified"),
            not f.name.endswith("_count"),
            len(f.name),
            f.name,
        )
    )
    return (f"{be.explore.base_alias}.{measures[0].name}", measures[0]) if measures else None


def _model_name(em: EffectiveModel) -> str:
    return em.model_files[0].rsplit("/", 1)[-1].removesuffix(".model.lkml")


def _dump(obj: Any) -> str:
    return yaml.safe_dump(
        obj, sort_keys=False, allow_unicode=True, width=100, default_flow_style=False
    )


def _spec_text(
    project: str, agent: str, bound: list[BoundExplore], skipped: list[str]
) -> tuple[str, list[str]]:
    front = {
        "agent": agent,
        "description": f"Answers questions on {project} data. TODO: describe the audience and scope.",
        "explores": [f"{project}::{b.name}" for b in bound],
        "derive": {"lookml_descriptions": True, "catalog_glossary": True},
    }
    lines = ["---", _dump(front).rstrip()]
    if skipped:
        lines.append(
            "# more explores exist than a CA agent can use (max 5); not included: "
            + ", ".join(skipped)
        )
    lines += [
        "---",
        "",
        "## Role",
        f"You are an analyst answering questions about {project}. TODO: set the persona and tone.",
        "",
        "## Audience",
        "TODO: who asks the questions, and what they already know.",
        "",
    ]
    pii = sorted(
        {
            f.display_label().lower()
            for be in bound
            for _, f in exposed_fields(be)
            if f.has_tag("pii") and not f.hidden
        }
    )
    if pii:
        lines += ["## Guardrails", f"- Never return {', '.join(pii)}.", ""]
    golden: list[dict[str, Any]] = []
    for be in bound:
        sm = _starter_measure(be)
        if sm is None:
            continue
        ref, f = sm
        golden.append(
            {
                "id": f"gq-{be.name.replace('_', '-')}-total",
                "questions": [f"What is the total {f.display_label().lower()}?"],
                "looker_query": {
                    "model": _model_name(be.model),
                    "explore": be.name,
                    "fields": [ref],
                },
            }
        )
    if golden:
        lines += ["## Golden queries", _dump(golden).rstrip(), ""]
    return "\n".join(lines).rstrip() + "\n", [q["id"] for q in golden]


def _suite_text(agent: str, bound: list[BoundExplore]) -> str:
    tests = []
    for be in bound[:1]:
        sm = _starter_measure(be)
        if sm is None:
            continue
        ref, f = sm
        tests.append(
            {
                "id": f"{agent}-001",
                "question": f"What is the total {f.display_label().lower()}?",
                "agent": agent,
                "expect": {"explore": be.name, "fields_any_of": [[ref]]},
                "tags": ["starter"],
            }
        )
    header = (
        f"# Golden suite for {agent}. Replace the starter test with real business questions.\n"
        "# Add `ground_truth_sql: sql/<id>.sql` (warehouse SQL on raw tables, independent of LookML;\n"
        "# `$as_of` becomes the pinned date) to check answers, not only structure. See docs/own-looker.md.\n"
    )
    return header + _dump({"version": 1, "tests": tests})


def scaffold(
    root: Path,
    projects: dict[str, Path],
    *,
    as_of: dt.date,
    warehouse: str = "duckdb",
    force: bool = False,
) -> ScaffoldResult:
    if not projects:
        raise ScaffoldError(
            "no LookML projects given or found (use --project name=path or --scan DIR)"
        )
    root = root.resolve()
    res = ScaffoldResult(root=root, projects={n: p.resolve() for n, p in projects.items()})
    rel = {n: os.path.relpath(p, root) for n, p in res.projects.items()}

    # resolve with absolute paths: the target directory may not exist yet
    cfg = LkagentConfig(
        name=root.name or "lkagent",
        as_of=as_of,
        projects={n: ProjectConfig(path=str(p)) for n, p in res.projects.items()},
        owners=None,
    )
    cfg.root = root
    ws = load_workspace(cfg)  # raises on import cycles
    files: dict[str, str] = {}
    for name in sorted(projects):
        if not ws.projects[name].model_files:
            res.libraries.append(name)
            continue
        em = resolve_project(ws, name)
        explores: list[LExplore] = [e for e in em.queryable_explores() if not e.hidden]
        if not explores:
            res.notes.append(f"{name}: no queryable explores; no agent created")
            continue
        bound = [BoundExplore(name, e, em) for e in explores[:MAX_EXPLORES]]
        skipped = [e.name for e in explores[MAX_EXPLORES:]]
        agent = agent_id_for(name)
        text, _ = _spec_text(name, agent, bound, skipped)
        files[f"agents/{agent}.agent.md"] = text
        files[f"suites/{agent}.yaml"] = _suite_text(agent, bound)
        res.agents[agent] = [f"{name}::{b.name}" for b in bound]
        if skipped:
            res.notes.append(
                f"{agent}: {len(skipped)} explore(s) beyond the CA limit of {MAX_EXPLORES} were left "
                "out; split them into another agent if needed"
            )

    config: dict[str, Any] = {
        "version": 2,
        "name": root.name or "lkagent",
        "as_of": as_of.isoformat(),
        "projects": {n: {"path": r} for n, r in sorted(rel.items())},
        "agents": {"paths": ["agents/**/*.agent.md"]},
        "catalogs": {"glossary": {"adapter": "yaml", "path": "catalog/glossary.yaml"}},
        "suites": {a: f"suites/{a}.yaml" for a in sorted(res.agents)},
        "owners": "owners.yaml",
        "build": {"out_dir": "build"},
        "diagnose": {
            "runner": "ca",
            "history": ".lkagent/history.duckdb",
            "ca": {
                "location": "global",
                "context_version": "PUBLISHED",
                "agents": {a: f"TODO-{a}-data-agent-id" for a in sorted(res.agents)},
            },
            "ground_truth": {"engine": warehouse},
        },
    }
    files["lkagent.yaml"] = (
        "# lookml-agentops config generated by `lkagent init`. Paths are relative to this file.\n"
        '# as_of pins relative dates ("last quarter") so answers are stable; move it deliberately.\n'
        "# diagnose.ca.agents maps each agent id to its Conversational Analytics data agent.\n"
        + _dump(config)
    )
    owners = [{"match": f"lookml:{n}/**", "team": f"TODO-owner-of-{n}"} for n in sorted(rel)]
    owners += [{"match": f"agent:{a}", "team": f"TODO-owner-of-{a}"} for a in sorted(res.agents)]
    owners += [
        {"match": "catalog:*", "team": "TODO-data-governance"},
        {"match": "runner:*", "team": "TODO-bi-platform"},
        {"match": "data:*", "team": "TODO-data-engineering"},
    ]
    files["owners.yaml"] = (
        "# Who owns each tracked input. Most specific match wins; path globs narrow a match, e.g.\n"
        '#   - match: "lookml:finance/views/revenue/**"\n#     team: revenue-analytics\n'
        + _dump({"owners": owners})
    )
    files["catalog/glossary.yaml"] = (
        "# Business glossary (knowledge catalog export). linked_fields use <project>.<view>.<field>.\n"
        "# Example:\n"
        "#   - id: net_revenue\n#     name: Net Revenue\n#     definition: Gross revenue minus discounts and refunds.\n"
        "#     synonyms: [revenue, sales]\n#     linked_fields: [core.orders.net_revenue]\n#     status: approved\n"
        "version: 1\nterms: []\n"
    )
    existing = [p for p in files if (root / p).exists()]
    if existing and not force:
        raise ScaffoldError(
            f"would overwrite {', '.join(sorted(existing))}; pass --force to replace them"
        )
    for relpath, text in sorted(files.items()):
        out = root / relpath
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        res.written.append(out)
    return res
