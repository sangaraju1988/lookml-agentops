"""``lkagent diagnose impact``: which agents and tests a change can affect, before running anything.

The base and head states are loaded (git worktree for ``--base REF``, or an explicit config path),
every element is fingerprinted on both sides, and each head test's *static* dependency set
(expected fields and their closure, explores, spec elements its question exercises, catalog terms,
its own definition) is intersected with the changed element facets. Works for any topology: a
change in a project that others import affects tests of every agent whose explores reach it.
"""

from __future__ import annotations

import contextlib
import re
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, Field

from lookml_agentops.catalog import load_catalog
from lookml_agentops.config import LkagentConfig, load_config
from lookml_agentops.diagnose.deps import test_deps
from lookml_agentops.diagnose.elements import (
    Element,
    Facet,
    catalog_elements,
    model_elements,
    spec_elements,
)
from lookml_agentops.diagnose.loader import LoadedTest
from lookml_agentops.diagnose.modeldiff import ElementChange, diff_elements
from lookml_agentops.diagnose.models import AgentAnswer, RunnerMeta
from lookml_agentops.diagnose.run import plan_tests
from lookml_agentops.generate.compile import CompiledAgent, compile_agents
from lookml_agentops.inputs.declared import load_owners
from lookml_agentops.lookml.resolve import EffectiveModel
from lookml_agentops.spec.interpret import normalize


class ImpactError(Exception):
    pass


class ImpactReport(BaseModel):
    base: str
    head: str
    changes: list[ElementChange]
    change_owners: dict[str, str] = Field(default_factory=dict)  # element_id -> owner
    affected: dict[str, list[str]] = Field(default_factory=dict)  # agent -> test ids
    reasons: dict[str, list[str]] = Field(default_factory=dict)  # test id -> changed element ids

    def test_ids(self) -> set[str]:
        return {t for ts in self.affected.values() for t in ts}


def _git(args: list[str], cwd: Path) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=60
        )
    except subprocess.CalledProcessError as exc:
        raise ImpactError(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc
    return out.stdout.strip()


@contextlib.contextmanager
def config_at_ref(cfg: LkagentConfig, ref: str) -> Iterator[LkagentConfig]:
    """The same lkagent.yaml, as it was at git ``ref`` (monorepo layout; see docs for multi-repo)."""
    top = Path(_git(["rev-parse", "--show-toplevel"], cfg.root)).resolve()
    rel = cfg.root.resolve().relative_to(top)
    with tempfile.TemporaryDirectory(prefix="lkagent-impact-") as tmp:
        wt = Path(tmp) / "wt"
        _git(["worktree", "add", "--detach", str(wt), ref], top)
        try:
            yield load_config(wt / rel)
        finally:
            with contextlib.suppress(ImpactError):
                _git(["worktree", "remove", "--force", str(wt)], top)


def _content(eid: str, kind: str, input_id: str, version: str, location: str | None) -> Element:
    return Element(
        element_id=eid,
        kind=kind,
        facets={
            "content": Facet(
                version=version,
                input_id=input_id,
                location=location,
                params={"value": {"v": version, "loc": location or ""}},
            )
        },
    )


def snapshot(
    cfg: LkagentConfig,
) -> tuple[dict[str, CompiledAgent], list[LoadedTest], dict[str, Element]]:
    agents = compile_agents(cfg)
    models: dict[str, EffectiveModel] = {}
    for ca in agents.values():
        models.update(ca.models)
    tests = plan_tests(cfg, agents)
    elements = model_elements(models)
    for ca in agents.values():
        elements.update(spec_elements(ca.spec))
    entry = cfg.catalog()
    if entry is not None:
        elements.update(catalog_elements(load_catalog(cfg), entry[0], entry[1].path))
    for lt in tests:
        elements[f"test:{lt.test.id}"] = _content(
            f"test:{lt.test.id}", "test", lt.source, lt.test_hash, lt.location
        )
    return agents, tests, elements


def static_exercised(lt: LoadedTest, ca: CompiledAgent) -> list[str]:
    """Spec elements a question exercises, found without running an agent."""
    text = " " + re.sub(r"[^a-z0-9\s-]", " ", lt.test.question.lower()) + " "
    out = list(lt.test.rule_ids)
    for r in ca.spec.active_rules():
        if any(f" {c.subject} " in text for c in r.claims):
            out.append(f"rule:{r.rule_id}")
    for v in ca.spec.vocabulary:
        if any(f" {normalize(p)} " in text for p in v.phrases):
            out.append(f"vocab:{v.vocab_id}")
    if lt.test.expect.refuse_or_exclude_tags:
        out += [f"guardrail:{g.guardrail_id}" for g in ca.spec.guardrails]
    return sorted(set(out))


def analyze(
    base_cfg: LkagentConfig, head_cfg: LkagentConfig, *, base_label: str, head_label: str
) -> ImpactReport:
    _, _, base_el = snapshot(base_cfg)
    agents, tests, head_el = snapshot(head_cfg)
    catalog = load_catalog(head_cfg)
    owners = load_owners(head_cfg)
    changes = diff_elements(base_el, head_el)
    changed = {(c.element_id, c.facet) for c in changes}
    changed_ids = {c.element_id for c in changes}
    report = ImpactReport(base=base_label, head=head_label, changes=changes)
    for c in changes:
        loc = (c.location or "").rsplit(":", 1)[0]
        path = (
            loc.split("/", 1)[1] if c.input_id.startswith("lookml:") and "/" in loc else loc or None
        )
        report.change_owners[c.element_id] = owners.owner(c.input_id, path)
    for lt in tests:
        ca = agents[lt.test.agent]
        ans = AgentAnswer(
            meta=RunnerMeta(runner="static", runner_version="static"),
            applied_rules=static_exercised(lt, ca),
        )
        deps = test_deps(
            lt,
            ans,
            ca.binding,
            catalog,
            runner_element="runner:static",
            has_truth=lt.sql_path is not None,
        )
        hits = sorted(
            {
                d.element_id
                for d in deps
                if d.rank < 3
                and d.element_id in changed_ids
                and any((d.element_id, f) in changed for f in d.facets)
            }
        )
        if f"test:{lt.test.id}" not in base_el:
            hits.append(f"test:{lt.test.id} (new)")
        if hits:
            report.affected.setdefault(lt.test.agent, []).append(lt.test.id)
            report.reasons[lt.test.id] = hits
    for a in report.affected:
        report.affected[a].sort()
    return report


def render_text(r: ImpactReport) -> str:
    lines = [f"impact {r.base} -> {r.head}: {len(r.changes)} changed element facet(s)"]
    for c in r.changes:
        lines.append(f"  {c.describe()}  owner={r.change_owners.get(c.element_id, 'unassigned')}")
    n = len(r.test_ids())
    lines.append(f"{n} affected test(s) in {len(r.affected)} agent(s)")
    for agent, tids in sorted(r.affected.items()):
        lines.append(f"  {agent}: {', '.join(tids)}")
    return "\n".join(lines) + "\n"
