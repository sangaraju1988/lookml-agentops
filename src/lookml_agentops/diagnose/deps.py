"""Dependency tracing: the set of elements (and which facets of them) each test depends on.

For every test we record:

* the spec elements it exercises (rules, vocabulary, guardrails, golden queries) — rank 0;
* the LookML fields used by the expected query and by the actual answer — rank 0 — plus the
  fields they reference through ``${...}`` (rank 1), the explores involved and the fields in their
  guards (rank 1);
* catalog terms linked to those fields (rank 2), the agent's whole-spec context (rank 3);
* the test definition and its ground-truth result (rank 1), and the runner (rank 4).

``facets`` says which part of an element matters: result-checked tests depend on the
``semantic`` facet (SQL etc.) and the ``surface`` facet (labels, descriptions — what an agent
picks from); structure-only tests depend only on ``surface``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel

from lookml_agentops.catalog import Catalog
from lookml_agentops.diagnose.loader import LoadedTest
from lookml_agentops.diagnose.models import AgentAnswer
from lookml_agentops.generate.bind import Binding, BoundExplore
from lookml_agentops.lookml.model import LField

REF_RE = re.compile(r"\$\{([^}]+)\}")

RANK = {
    "exercised": 0,
    "answer": 0,
    "expected": 0,
    "via": 1,
    "explore": 1,
    "guard": 1,
    "test": 1,
    "data": 1,
    "term": 2,
    "context": 3,
    "runner": 4,
}


class Dep(BaseModel):
    element_id: str
    facets: list[str]
    role: str
    rank: int


@dataclass
class _Ctx:
    binding: Binding
    catalog: Catalog


def _lookup(be: BoundExplore, alias: str, name: str) -> LField | None:
    view = be.model.explore_views(be.explore).get(alias)
    if view is None:
        return None
    if name in view.fields:
        return view.fields[name]
    for f in view.fields.values():
        if f.kind == "dimension_group" and name.startswith(f.name + "_"):
            return f
    return None


def _closure(
    be: BoundExplore, alias: str, f: LField, seen: set[tuple[str, str]]
) -> list[tuple[str, LField]]:
    """Fields referenced (transitively) by ``f``'s SQL and filters, as (alias, field)."""
    out: list[tuple[str, LField]] = []
    texts = [f.sql or ""]
    filters = f.params.get("filters")
    if isinstance(filters, list):
        texts += [f"${{{k}}}" for item in filters if isinstance(item, dict) for k in item]
    for text in texts:
        for ref in REF_RE.findall(text):
            ref = ref.strip()
            if ref == "TABLE":
                continue
            a, _, n = ref.rpartition(".")
            a = a or alias
            g = _lookup(be, a, n)
            if g is None or (a, g.name) in seen:
                continue
            seen.add((a, g.name))
            out.append((a, g))
            out += _closure(be, a, g, seen)
    return out


def test_deps(
    lt: LoadedTest,
    ans: AgentAnswer,
    binding: Binding,
    catalog: Catalog,
    *,
    runner_element: str,
    has_truth: bool,
) -> list[Dep]:
    t = lt.test
    exp = t.expect
    facets = ["semantic", "surface"] if has_truth else ["surface"]
    deps: dict[str, Dep] = {}

    def add(element_id: str, role: str, fs: list[str]) -> None:
        rank = RANK[role]
        prev = deps.get(element_id)
        if prev is None or rank < prev.rank:
            deps[element_id] = Dep(
                element_id=element_id,
                facets=sorted(set(fs) | set(prev.facets if prev else [])),
                role=role,
                rank=rank,
            )
        else:
            prev.facets = sorted(set(prev.facets) | set(fs))

    for el in [*t.rule_ids, *(a for a in ans.applied_rules if not a.startswith("external:"))]:
        add(el, "exercised", ["content"])
    add(f"spec:{t.agent}", "context", ["content"])
    add(f"test:{t.id}", "test", ["content"])
    add(runner_element, "runner", ["content"])
    if has_truth:
        add(f"gt:{t.id}", "data", ["content"])

    explores = [e for e in {exp.explore, ans.explore} if e]
    if not explores and binding.explores:
        explores = [binding.explores[0].name]
    refs: list[tuple[str, str]] = []  # (role, alias.field)
    for opt in exp.fields_any_of or []:
        refs += [("expected", r) for r in opt]
    refs += [("expected", f.field) for f in exp.filters]
    refs += [("answer", r) for r in ans.fields]
    refs += [("answer", f.field) for f in ans.filters]
    for ename in explores:
        be = binding.explore(ename)
        if be is None:
            continue
        scope = be.project
        explore_facets = ["semantic", "surface"] if (has_truth or exp.sql_contains) else ["surface"]
        add(f"explore:{scope}/{be.name}", "explore", explore_facets)
        seen: set[tuple[str, str]] = set()
        fields: list[tuple[str, str, LField]] = []
        for role, ref in refs:
            alias, _, name = ref.partition(".")
            f = _lookup(be, alias, name)
            if f is None:
                continue
            fields.append((role, alias, f))
            seen.add((alias, f.name))
            fields += [("via", a, g) for a, g in _closure(be, alias, f, seen)]
        if has_truth and be.explore.sql_always_where:
            for ref in REF_RE.findall(be.explore.sql_always_where):
                a, _, n = ref.strip().rpartition(".")
                g = _lookup(be, a or be.explore.base_alias, n)
                if g is not None:
                    fields.append(("guard", a, g))
        for role, _alias, f in fields:
            add(f"field:{scope}/{f.view}.{f.name}", role, facets)
            for term in catalog.terms_linking(f.origin_project, f.view, f.name):
                add(f"term:{term.id}", "term", ["content"])
    return sorted(deps.values(), key=lambda d: (d.rank, d.element_id))
