"""Bind an (unbound) AgentSpec to the resolved LookML models and the catalog.

Binding resolves every Explore, field reference, rule claim and guardrail against the effective
models, fills derived context when ``derive`` is on, and reports spec-level findings (LKS0xx).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from lookml_agentops.catalog import Catalog
from lookml_agentops.lookml.model import LExplore, LField, expand_field_names
from lookml_agentops.lookml.resolve import EffectiveModel
from lookml_agentops.spec.interpret import normalize
from lookml_agentops.spec.model import (
    AgentSpec,
    DerivedContext,
    DerivedDescription,
    DerivedTerm,
    LookerQuery,
    SourceRef,
)

MAX_EXPLORES = 5  # documented CA limit for Looker data agents


@dataclass(frozen=True)
class SpecFinding:
    rule_id: str
    severity: str
    message: str
    file: str
    line: int
    obj: str


@dataclass
class BoundExplore:
    project: str
    explore: LExplore
    model: EffectiveModel

    @property
    def name(self) -> str:
        return self.explore.name


@dataclass
class Binding:
    spec: AgentSpec
    explores: list[BoundExplore] = field(default_factory=list)
    findings: list[SpecFinding] = field(default_factory=list)

    @property
    def errors(self) -> list[SpecFinding]:
        return [f for f in self.findings if f.severity == "error"]

    def explore(self, name: str) -> BoundExplore | None:
        return next((b for b in self.explores if b.name == name), None)

    def field_ref(self, explore: str, ref: str) -> LField | None:
        """Look up ``alias.field`` (timeframes allowed) in one of the agent's explores."""
        b = self.explore(explore)
        if b is None or "." not in ref:
            return None
        alias, name = ref.split(".", 1)
        view = b.model.explore_views(b.explore).get(alias)
        if view is None:
            return None
        if name in view.fields:
            return view.fields[name]
        for f in view.fields.values():
            if f.kind == "dimension_group" and any(n == name for n, _ in expand_field_names(f)):
                return f
        return None


def _model_name(em: EffectiveModel) -> str:
    return (
        em.model_files[0].rsplit("/", 1)[-1].removesuffix(".model.lkml")
        if em.model_files
        else em.project
    )


def default_time(b: BoundExplore) -> tuple[str, LField] | None:
    view = b.model.views.get(b.explore.base_view)
    if view is None:
        return None
    for f in sorted(view.fields.values(), key=lambda x: x.name):
        if f.kind == "dimension_group" and f.has_tag("ai_default_time"):
            return b.explore.base_alias, f
    return None


def fiscal_alias(b: BoundExplore, alias: str, group: str) -> str | None:
    for j in b.explore.joins.values():
        v = b.model.views.get(j.from_view)
        if (
            v is not None
            and "fiscal_calendar" in v.tags
            and f"${{{alias}.{group}_" in (j.sql_on or "")
        ):
            return j.name
    return None


def exposed_fields(b: BoundExplore) -> list[tuple[str, LField]]:
    """(alias.field, field) for every field of every view in the explore."""
    out = []
    for alias, v in b.model.explore_views(b.explore).items():
        for f in v.fields.values():
            out.append((f"{alias}.{f.name}", f))
    return out


def preferred_alias(b: BoundExplore, view: str) -> str | None:
    """Alias for a view in an explore: the fiscal alias for the calendar, else base, else first."""
    aliases = [a for a, v in b.model.explore_views(b.explore).items() if v.name == view]
    if not aliases:
        return None
    dt = default_time(b)
    if dt is not None:
        fa = fiscal_alias(b, dt[0], dt[1].name)
        if fa in aliases:
            return fa
    if b.explore.base_alias in aliases:
        return b.explore.base_alias
    return aliases[0]


class Binder:
    def __init__(self, models: dict[str, EffectiveModel], catalog: Catalog) -> None:
        self.models = models
        self.catalog = catalog

    def bind(self, spec: AgentSpec) -> Binding:
        spec = spec.model_copy(deep=True)
        b = Binding(spec=spec)

        def add(rule: str, sev: str, msg: str, src: SourceRef, obj: str) -> None:
            b.findings.append(SpecFinding(rule, sev, msg, src.file, src.line, obj))

        # ---- explores ------------------------------------------------------------------
        if len(spec.explores) > MAX_EXPLORES:
            add(
                "LKS003",
                "error",
                f"agent {spec.agent_id} lists {len(spec.explores)} explores; a CA data "
                f"agent can connect to at most {MAX_EXPLORES}",
                spec.source,
                spec.agent_id,
            )
        for eb in spec.explores:
            em = self.models.get(eb.project)
            e = em.explores.get(eb.explore) if em else None
            if em is None or e is None or e.extension_required:
                why = (
                    "unknown project"
                    if em is None
                    else ("extension: required" if e else "not found")
                )
                add(
                    "LKS002",
                    "error",
                    f"explore {eb.project}::{eb.explore} is {why} in the resolved model",
                    spec.source,
                    f"{eb.project}::{eb.explore}",
                )
                continue
            bound = BoundExplore(eb.project, e, em)
            b.explores.append(bound)
            eb.model = _model_name(em)
            eb.label = e.label
            eb.description = e.description
            dt = default_time(bound)
            if dt:
                eb.default_time_field = f"{dt[0]}.{dt[1].name}"
                eb.fiscal_alias = fiscal_alias(bound, dt[0], dt[1].name)

        # ---- vocabulary entries ----------------------------------------------------------
        for v in spec.vocabulary:
            res = self.resolve_target(b, v.target)
            if res is None:
                add(
                    "LKS004",
                    "error",
                    f"vocabulary target {v.target!r} is not a field of this agent's explores",
                    v.source,
                    v.vocab_id,
                )
            else:
                v.explore, v.field = res

        # ---- rule claims + explicit field references --------------------------------------
        for r in spec.rules:
            for c in r.claims:
                if c.kind == "vocabulary":
                    res = self.resolve_phrase(b, c.value)
                    if res:
                        c.explore, c.field = res
            for ref in re.findall(r"`([A-Za-z_][\w]*\.[A-Za-z_][\w]*)`", r.text):
                if self.resolve_target(b, ref) is None:
                    add(
                        "LKS004",
                        "error",
                        f"rule {r.rule_id!r} names field `{ref}`, which does not exist in "
                        "this agent's explores",
                        r.source,
                        r.rule_id,
                    )
        # locked rule vs vocabulary entries
        for r in spec.rules:
            if not r.locked:
                continue
            for c in r.claims:
                for v in spec.vocabulary:
                    if (
                        v.origin_agent != r.origin_agent
                        and c.subject in v.phrases
                        and c.field
                        and v.field
                        and (c.field.split(".")[-1] != v.field.split(".")[-1])
                    ):
                        add(
                            "LKS008",
                            "error",
                            f"vocabulary {v.phrases} → {v.target} contradicts locked rule "
                            f"{r.rule_id!r} ({r.source})",
                            v.source,
                            v.vocab_id,
                        )

        # ---- golden queries --------------------------------------------------------------
        names = {x.name for x in b.explores}
        for q in spec.golden_queries:
            lq = q.looker_query
            if lq is None:
                add(
                    "LKS012",
                    "warning",
                    f"golden query {q.golden_id!r} uses an Explore URL with no cached "
                    "resolution; run `lkagent generate resolve-golden`",
                    q.source,
                    q.golden_id,
                )
                continue
            if lq.explore not in names:
                add(
                    "LKS006",
                    "error",
                    f"golden query {q.golden_id!r} uses explore {lq.explore!r}, which is not "
                    "one of this agent's explores",
                    q.source,
                    q.golden_id,
                )
                continue
            be = b.explore(lq.explore)
            if be is not None and lq.model != _model_name(be.model):
                add(
                    "LKS006",
                    "error",
                    f"golden query {q.golden_id!r} names model {lq.model!r}; explore "
                    f"{lq.explore} lives in model {_model_name(be.model)!r}",
                    q.source,
                    q.golden_id,
                )
            if lq.pivots:
                add(
                    "LKS005",
                    "error",
                    f"golden query {q.golden_id!r} uses pivots {lq.pivots}; Conversational "
                    "Analytics does not generate pivoted queries",
                    q.source,
                    q.golden_id,
                )
            refs = [
                *lq.fields,
                *(f.field for f in lq.filters),
                *(s.split()[0] for s in lq.sorts),
                *lq.pivots,
            ]
            for ref in refs:
                if b.field_ref(lq.explore, ref) is None:
                    add(
                        "LKS004",
                        "error",
                        f"golden query {q.golden_id!r} references unknown field {ref!r} in "
                        f"explore {lq.explore}",
                        q.source,
                        q.golden_id,
                    )

        # ---- guardrail coverage ----------------------------------------------------------
        for g in spec.guardrails:
            covered = []
            for be in b.explores:
                for ref, f in exposed_fields(be):
                    kinds = {t.split(":", 1)[1] for t in f.tags if t.startswith("pii:")}
                    if f.has_tag("pii") and (
                        kinds & set(g.pii_kinds) or normalize(f.display_label()) in g.text.lower()
                    ):
                        covered.append(f"{be.name}/{ref}")
            g.covers = sorted(set(covered))
        covered_all = {c for g in spec.guardrails for c in g.covers}
        for be in b.explores:
            for ref, f in exposed_fields(be):
                if f.has_tag("pii") and not f.hidden and f"{be.name}/{ref}" not in covered_all:
                    add(
                        "LKS010",
                        "error",
                        f"explore {be.name} exposes PII field {ref} but no guardrail covers it",
                        spec.source,
                        f"{be.name}/{ref}",
                    )

        # ---- placement advice ------------------------------------------------------------
        for v in spec.vocabulary:
            if not v.field or not v.explore:
                continue
            be = b.explore(v.explore)
            for phrase in v.phrases:
                hit = self.resolve_phrase(b, phrase, include_vocab=False)
                if hit is None:
                    continue
                if hit[1].split(".")[-1] == v.field.split(".")[-1]:
                    add(
                        "LKS009",
                        "warning",
                        f"vocabulary {phrase!r} duplicates a LookML label or catalog synonym "
                        f"for {v.field}; keep field synonyms in LookML",
                        v.source,
                        v.vocab_id,
                    )
                elif be is not None:
                    add(
                        "LKS009",
                        "warning",
                        f"vocabulary {phrase!r} → {v.field} contradicts LookML/catalog, "
                        f"which map it to {hit[1]}; move the definition into LookML",
                        v.source,
                        v.vocab_id,
                    )

        # ---- derived context -------------------------------------------------------------
        if spec.derive:
            spec.derived = self.derive(b)
        return b

    # ---- resolution helpers --------------------------------------------------------------
    def resolve_target(self, b: Binding, target: str) -> tuple[str, str] | None:
        """``explore.field`` or ``view.field`` (or ``alias.field``) -> (explore, alias.field)."""
        if "." not in target:
            return None
        head, name = target.split(".", 1)
        be = b.explore(head)
        if be is not None:
            hits = [
                (ref, f)
                for ref, f in exposed_fields(be)
                if ref.split(".", 1)[1] == name or any(n == name for n, _ in expand_field_names(f))
            ]
            if len(hits) == 1:
                ref = (
                    hits[0][0]
                    if hits[0][1].kind != "dimension_group"
                    else f"{hits[0][0].split('.')[0]}.{name}"
                )
                return be.name, ref
            return None
        for be in b.explores:
            if b.field_ref(be.name, target) is not None:
                return be.name, target
            alias = preferred_alias(be, head)
            if alias is not None and b.field_ref(be.name, f"{alias}.{name}") is not None:
                return be.name, f"{alias}.{name}"
        return None

    def phrase_index(self, b: Binding, *, include_vocab: bool = True) -> dict[str, tuple[str, str]]:
        """normalized phrase -> (explore, alias.field) from labels, catalog terms and vocabulary."""
        index: dict[str, tuple[str, str]] = {}
        for be in b.explores:
            for ref, f in exposed_fields(be):
                if f.hidden or f.kind == "dimension_group":
                    continue
                alias = preferred_alias(be, b_view(be, ref))
                use = f"{alias}.{f.name}" if alias else ref
                index.setdefault(normalize(f.display_label()), (be.name, use))
                for term in self.catalog.terms_linking(f.origin_project, f.view, f.name):
                    if term.approved:
                        for p in [term.name, *term.synonyms]:
                            index.setdefault(normalize(p), (be.name, use))
        if include_vocab:
            for v in b.spec.vocabulary:
                if v.field and v.explore:
                    for p in v.phrases:
                        index[normalize(p)] = (v.explore, v.field)
        return index

    def resolve_phrase(
        self, b: Binding, text: str, *, include_vocab: bool = True
    ) -> tuple[str, str] | None:
        return self.phrase_index(b, include_vocab=include_vocab).get(normalize(text))

    def derive(self, b: Binding) -> DerivedContext:
        spec = b.spec
        out = DerivedContext()
        seen_terms: set[str] = set()
        seen_desc: set[str] = set()
        for be in b.explores:
            for ref, f in exposed_fields(be):
                if f.hidden or f.has_tag("pii") or f.has_tag("ai_hidden"):
                    continue
                alias = preferred_alias(be, f.view) or ref.split(".")[0]
                use = f"{alias}.{f.name}"
                if (
                    spec.derive.get("lookml_descriptions")
                    and f.has_tag("ai_exposed")
                    and f.description
                ):
                    key = f"{be.name}/{use}"
                    if key not in seen_desc:
                        seen_desc.add(key)
                        loc = f.last_at
                        out.descriptions.append(
                            DerivedDescription(
                                field=use,
                                text=re.sub(r"\s+", " ", f.description.strip()),
                                source=f"lookml:{loc.project}/{loc.file}:{loc.line}",
                            )
                        )
                if spec.derive.get("catalog_glossary"):
                    for term in self.catalog.terms_linking(f.origin_project, f.view, f.name):
                        if term.approved and term.id not in seen_terms:
                            seen_terms.add(term.id)
                            out.glossary.append(
                                DerivedTerm(
                                    term=term.name,
                                    description=re.sub(r"\s+", " ", term.definition.strip()),
                                    synonyms=list(term.synonyms),
                                    field=use,
                                    source=f"catalog:{term.id}",
                                )
                            )
        out.glossary.sort(key=lambda t: t.term)
        out.descriptions.sort(key=lambda d: d.field)
        return out


def b_view(be: BoundExplore, ref: str) -> str:
    alias = ref.split(".", 1)[0]
    v = be.model.explore_views(be.explore).get(alias)
    return v.name if v else alias


def load_golden_cache(path: Path) -> dict[str, LookerQuery]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {url: LookerQuery.model_validate(q) for url, q in data.get("queries", {}).items()}


def apply_golden_cache(spec: AgentSpec, cache: dict[str, LookerQuery]) -> None:
    for q in spec.golden_queries:
        if q.looker_query is None and q.explore_url and q.explore_url in cache:
            q.looker_query = cache[q.explore_url]
            q.resolved_from = "url-cache"
