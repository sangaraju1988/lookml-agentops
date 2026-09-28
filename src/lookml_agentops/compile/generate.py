"""Deterministic generation of layered agent instructions from LookML + glossary metadata.

Every rule is templated from metadata (descriptions, tags, glossary, always_filter,
sql_always_where, fiscal calendar, certified tags). No LLM is involved; ``--polish`` may only
rewrite prose afterwards (see :mod:`lookml_agentops.compile.polish`).

Layering: a rule belongs to the layer of the project that *originated* its source (a hub field
refined only in wording stays hub-originated for metric/vocabulary rules). Hub-layer rules refer
to hub explore names (e.g. ``orders_base``) so the hub layer does not change when a spoke renames
its own explores.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from lookml_agentops._util.hashing import sha256_obj
from lookml_agentops.catalog import Catalog, GlossaryTerm
from lookml_agentops.compile.schema import (
    AgentInstructions,
    ExampleQuestion,
    ExploreRef,
    FieldGuidance,
    GlossaryEntry,
    InstructionRule,
    Layer,
    Provenance,
)
from lookml_agentops.lookml.model import LExplore, LField, Loc
from lookml_agentops.lookml.resolve import EffectiveModel

PII_PHRASES = {
    "email": ["email", "e-mail", "email address"],
    "phone": ["phone", "phone number", "telephone"],
    "dob": ["date of birth", "birthday", "dob"],
    "address": ["address", "billing address", "street address"],
    "name": ["contact name", "contact person"],
    "ssn": ["ssn", "social security number"],
}

RELATIVE_PERIODS = {
    "last quarter": "last_completed_fiscal_quarter",
    "last fiscal quarter": "last_completed_fiscal_quarter",
    "previous quarter": "last_completed_fiscal_quarter",
    "last year": "last_completed_fiscal_year",
    "last fiscal year": "last_completed_fiscal_year",
    "last month": "last_completed_month",
}


class CompileError(Exception):
    pass


def _prov(loc: Loc, term: str | None = None) -> Provenance:
    return Provenance(project=loc.project, file=loc.file, line=loc.line, glossary_term_id=term)


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def measure_signature(f: LField) -> str:
    return sha256_obj({"type": f.type, "sql": _norm(f.sql), "filters": f.params.get("filters")})[
        :16
    ]


@dataclass
class _Ctx:
    hub: str
    spoke: str
    em: EffectiveModel
    hub_model: EffectiveModel
    catalog: Catalog

    def layer_of(self, project: str) -> str:
        return "hub" if project == self.hub else "spoke"

    @property
    def explores(self) -> list[LExplore]:
        return [e for e in self.em.queryable_explores() if not e.hidden]

    def usages(self, view: str, field: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for e in self.explores:
            for alias, v in self.em.explore_views(e).items():
                if v.name == view and field in v.fields:
                    out.append((e.name, alias))
        return out

    def alias_usages(self, view: str, field: str) -> list[str]:
        return sorted({f"{alias}.{field}" for _, alias in self.usages(view, field)})

    def reachable_fields(self) -> list[LField]:
        seen: dict[str, LField] = {}
        for e in self.explores:
            for v in self.em.explore_views(e).values():
                for f in v.fields.values():
                    seen.setdefault(f.id, f)
        return [seen[k] for k in sorted(seen)]

    def term_for(self, f: LField) -> GlossaryTerm | None:
        terms = self.catalog.by_id()
        cands = [terms[g] for g in f.glossary_ids() if g in terms]
        cands += self.catalog.terms_linking(f.origin_project, f.view, f.name)
        approved = [t for t in cands if t.approved]
        return approved[0] if approved else None

    def origin_explore(self, e: LExplore, param: str) -> str:
        """Deepest ancestor of ``e`` that carries the same value for ``param``."""
        value = e.params.get(param)
        origin = e.name
        for anc in self.em.ancestors(e.name)[1:]:
            pe = self.em.explores.get(anc)
            if pe is not None and pe.params.get(param) == value:
                origin = anc
        return origin

    def explore_loc(self, name: str) -> Loc:
        return self.em.explore_defs.get(name) or self.em.explores[name].provenance[0].loc

    def default_time(self, e: LExplore) -> tuple[str, LField] | None:
        base = self.em.views.get(e.base_view)
        if base is None:
            return None
        for f in sorted(base.fields.values(), key=lambda x: x.name):
            if f.kind == "dimension_group" and f.has_tag("ai_default_time"):
                return e.base_alias, f
        return None

    def fiscal_alias(self, e: LExplore, alias: str, group: str) -> str | None:
        for j in e.joins.values():
            v = self.em.views.get(j.from_view)
            if (
                v is not None
                and "fiscal_calendar" in v.tags
                and f"${{{alias}.{group}_" in (j.sql_on or "")
            ):
                return j.name
        return None

    def home_explore(self, view: str) -> LExplore | None:
        homes = [e for e in self.explores if e.base_view == view]
        if homes:
            return homes[0]
        for e in self.explores:
            if view in {v.name for v in self.em.explore_views(e).values()}:
                return e
        return None

    def spoke_explores_from(self, origin: str) -> list[LExplore]:
        return [e for e in self.explores if origin in self.em.ancestors(e.name)]


def _fields_in_sql(sql: str) -> list[tuple[str, str]]:
    return [(a, b) for a, b in re.findall(r"\$\{(\w+)\.(\w+)\}", sql)]


def _metric_rules(c: _Ctx) -> list[tuple[str, InstructionRule]]:
    out: list[tuple[str, InstructionRule]] = []
    for f in c.reachable_fields():
        if f.kind != "measure" or f.hidden:
            continue
        certified = f.has_tag("certified")
        if not (certified or f.has_tag("ai_exposed")):
            continue
        term = c.term_for(f)
        name = term.name if term else f.display_label()
        hub_f = c.hub_model.field(f.view, f.name) if f.origin_project == c.hub else None
        source = hub_f if hub_f is not None else f
        rid = f"metric.{f.view}.{f.name}"
        text = (
            f"{source.display_label()} ({f.view}.{f.name}) is the "
            f"{'certified ' if source.has_tag('certified') else ''}definition of {name}. "
            f"{_norm(source.description)}"
        ).strip()
        rule = InstructionRule(
            rule_id=rid,
            kind="metric_definition",
            text=text,
            provenance=_prov(source.defined_at, term.id if term else None),
            subjects=[f"metric:{f.view}.{f.name}"],
            assertion=measure_signature(source),
            certified=source.has_tag("certified"),
            data={"field": f"{f.view}.{f.name}", "label": source.display_label()},
        )
        out.append((c.layer_of(source.origin_project), rule))
        if hub_f is not None and measure_signature(hub_f) != measure_signature(f):
            # the spoke changed the definition of a hub measure: emit the spoke's claim so the
            # contradiction check can reject it (or accept it when the hub one is not certified)
            out.append(
                (
                    "spoke",
                    rule.model_copy(
                        update={
                            "rule_id": f"{rid}.{c.spoke}",
                            "text": f"{f.display_label()} ({f.view}.{f.name}) redefined by {c.spoke}. {_norm(f.description)}",
                            "provenance": _prov(f.last_at, term.id if term else None),
                            "assertion": measure_signature(f),
                            "certified": False,
                        }
                    ),
                )
            )
    return out


def _vocab_rules(c: _Ctx) -> list[tuple[str, InstructionRule]]:
    out: list[tuple[str, InstructionRule]] = []
    fields_by_key = {(f.origin_project, f.view, f.name): f for f in c.reachable_fields()}
    for term in c.catalog.terms:
        if not term.approved:
            continue
        linked: list[LField] = []
        for link in term.linked_fields:
            parts = link.split(".")
            if len(parts) == 3 and tuple(parts) in fields_by_key:
                linked.append(fields_by_key[(parts[0], parts[1], parts[2])])
        linked += [
            f for f in fields_by_key.values() if term.id in f.glossary_ids() and f not in linked
        ]
        if not linked:
            continue
        f = sorted(linked, key=lambda x: x.id)[0]
        phrases: list[str] = []
        for p in [term.name, *term.synonyms]:
            if p.lower() not in phrases:
                phrases.append(p.lower())
        quoted = ", ".join(f'"{p}"' for p in phrases)
        out.append(
            (
                c.layer_of(f.origin_project),
                InstructionRule(
                    rule_id=f"vocab.{term.id}",
                    kind="vocabulary",
                    text=f"{quoted} all mean {term.name}: {_norm(term.definition)} Use {f.view}.{f.name}.",
                    provenance=_prov(f.defined_at, term.id),
                    subjects=[f"vocab:{p}" for p in phrases],
                    assertion=f"{f.view}.{f.name}",
                    certified=f.has_tag("certified"),
                    data={
                        "field": f"{f.view}.{f.name}",
                        "view": f.view,
                        "name": f.name,
                        "kind": f.kind,
                        "phrases": phrases,
                        "term_id": term.id,
                    },
                ),
            )
        )
    return out


def _explore_rules(c: _Ctx) -> list[tuple[str, InstructionRule]]:
    out: dict[str, tuple[str, InstructionRule]] = {}
    for e in c.explores:
        if e.sql_always_where:
            origin = c.origin_explore(e, "sql_always_where")
            loc = c.explore_loc(origin)
            reasons: list[str] = []
            markers: list[str] = []
            for alias, fname in _fields_in_sql(e.sql_always_where):
                view = c.em.explore_views(e).get(alias)
                fld = view.fields.get(fname) if view else None
                if fld is not None:
                    if fld.description:
                        reasons.append(_norm(fld.description).rstrip("."))
                    markers += re.findall(r"\$\{TABLE\}\.(\w+)", fld.sql or "")
            label = c.em.explores[origin].label or origin
            rid = f"exclusion.{origin}"
            out[rid] = (
                c.layer_of(loc.project),
                InstructionRule(
                    rule_id=rid,
                    kind="exclusion",
                    text=(
                        f"Explores built on {origin} ({label}) always exclude rows: "
                        f"{'; '.join(reasons) or e.sql_always_where}. Never include them, even when "
                        "asked for all data."
                    ),
                    provenance=_prov(loc),
                    subjects=[f"exclusion:{origin}"],
                    assertion=sha256_obj(_norm(e.sql_always_where))[:16],
                    certified=c.layer_of(loc.project) == "hub",
                    data={
                        "explore": origin,
                        "sql": _norm(e.sql_always_where),
                        "sql_markers": sorted(set(markers)),
                    },
                ),
            )
        for field, value in sorted(e.always_filter.items()):
            origin = c.origin_explore(e, "always_filter")
            loc = c.explore_loc(origin)
            rid = f"default_filter.{origin}.{field}"
            alias, _, fname = field.partition(".")
            view = c.em.explore_views(e).get(alias)
            fld = view.fields.get(fname) if view else None
            desc = _norm(fld.description) if fld else ""
            label = fld.display_label() if fld else field
            out[rid] = (
                c.layer_of(loc.project),
                InstructionRule(
                    rule_id=rid,
                    kind="default_filter",
                    text=(
                        f'In explores built on {origin}, always filter {label} ({field}) = "{value}" '
                        f"unless the user explicitly asks otherwise. {desc}"
                    ).strip(),
                    provenance=_prov(loc),
                    subjects=[f"default_filter:{origin}:{field}"],
                    assertion=value,
                    certified=c.layer_of(loc.project) == "hub",
                    data={"explore": origin, "field": field, "value": value},
                ),
            )
        dt_ = c.default_time(e)
        if dt_ is not None:
            alias, grp = dt_
            origin = c.origin_explore(e, "view_name")
            # use the hub base explore name when the base view comes from it
            origin = next(
                (
                    a
                    for a in reversed(c.em.ancestors(e.name))
                    if c.em.explores[a].base_view == e.base_view
                ),
                origin,
            )
            fiscal = c.fiscal_alias(e, alias, grp.name)
            rid = f"time.default.{origin}"
            out[rid] = (
                c.layer_of(grp.origin_project),
                InstructionRule(
                    rule_id=rid,
                    kind="time_convention",
                    text=(
                        f"In explores built on {origin}, the default time field is "
                        f"{alias}.{grp.name} ({grp.display_label()}): {_norm(grp.description)}"
                        + (
                            f" Use {fiscal}.fiscal_quarter_label / {fiscal}.fiscal_year for fiscal periods."
                            if fiscal
                            else ""
                        )
                    ),
                    provenance=_prov(grp.defined_at),
                    subjects=[f"time:default:{origin}"],
                    assertion=f"{alias}.{grp.name}",
                    certified=False,
                    data={
                        "explore": origin,
                        "time_field": f"{alias}.{grp.name}",
                        "fiscal_alias": fiscal,
                    },
                ),
            )
    return [out[k] for k in sorted(out)]


def _fiscal_rules(c: _Ctx) -> list[tuple[str, InstructionRule]]:
    out: list[tuple[str, InstructionRule]] = []
    seen: set[str] = set()
    for e in c.explores:
        for v in c.em.explore_views(e).values():
            if "fiscal_calendar" not in v.tags or v.name in seen:
                continue
            seen.add(v.name)
            loc = v.provenance[0].loc
            desc = _norm(str(v.params.get("description", "")))
            phrases = ", ".join(f'"{k}" = {p}' for k, p in RELATIVE_PERIODS.items())
            out.append(
                (
                    c.layer_of(loc.project),
                    InstructionRule(
                        rule_id=f"time.fiscal.{v.name}",
                        kind="time_convention",
                        text=f"{desc} Relative periods always mean completed periods: {phrases}.",
                        provenance=_prov(loc),
                        subjects=["time:fiscal_calendar"],
                        assertion=sha256_obj(desc)[:16],
                        certified=c.layer_of(loc.project) == "hub",
                        data={"view": v.name, "relative_periods": dict(RELATIVE_PERIODS)},
                    ),
                )
            )
    return out


def _pii_rules(c: _Ctx) -> list[tuple[str, InstructionRule]]:
    out: list[tuple[str, InstructionRule]] = []
    for f in c.reachable_fields():
        if not f.has_tag("pii"):
            continue
        kinds = sorted(t.split(":", 1)[1] for t in f.tags if t.startswith("pii:"))
        phrases = sorted(
            {f.display_label().lower(), *(p for k in kinds for p in PII_PHRASES.get(k, []))}
        )
        out.append(
            (
                c.layer_of(f.origin_project),
                InstructionRule(
                    rule_id=f"pii.{f.view}.{f.name}",
                    kind="pii_guardrail",
                    text=(
                        f"Never return {f.display_label()} ({f.view}.{f.name}; personal data: "
                        f"{', '.join(kinds) or 'pii'}). If asked for it, refuse or answer without it."
                    ),
                    provenance=_prov(f.defined_at),
                    subjects=[f"pii:{f.view}.{f.name}"],
                    assertion="deny",
                    certified=True,
                    data={"field": f"{f.view}.{f.name}", "phrases": phrases},
                ),
            )
        )
    return out


def _glossary_and_guidance(
    c: _Ctx,
) -> tuple[dict[str, list[GlossaryEntry]], dict[str, list[FieldGuidance]]]:
    glossary: dict[str, list[GlossaryEntry]] = {"hub": [], "spoke": []}
    guidance: dict[str, list[FieldGuidance]] = {"hub": [], "spoke": []}
    fields = c.reachable_fields()
    for term in c.catalog.terms:
        if not term.approved:
            continue
        linked = [
            f
            for f in fields
            if f"{f.origin_project}.{f.view}.{f.name}" in term.linked_fields
            or term.id in f.glossary_ids()
        ]
        if not linked:
            continue
        layer = c.layer_of(sorted(linked, key=lambda x: x.id)[0].origin_project)
        glossary[layer].append(
            GlossaryEntry(
                term_id=term.id,
                name=term.name,
                definition=_norm(term.definition),
                synonyms=list(term.synonyms),
                fields=sorted({u for f in linked for u in c.alias_usages(f.view, f.name)}),
            )
        )
    for f in fields:
        if not f.has_tag("ai_exposed") or f.hidden:
            continue
        guidance[c.layer_of(f.last_at.project)].append(
            FieldGuidance(
                field=f.id,
                kind=f.kind,
                label=f.display_label(),
                description=_norm(f.description),
                usages=c.alias_usages(f.view, f.name),
                tags=sorted(
                    t for t in f.tags if t in ("certified", "pii") or t.startswith("glossary:")
                ),
            )
        )
    return glossary, guidance


def _examples(
    c: _Ctx, rules: Iterable[tuple[str, InstructionRule]]
) -> dict[str, list[ExampleQuestion]]:
    out: dict[str, list[ExampleQuestion]] = {"hub": [], "spoke": []}
    for layer, r in rules:
        if r.kind != "metric_definition" or not r.certified:
            continue
        view, _, name = str(r.data["field"]).partition(".")
        home = c.home_explore(view)
        if home is None:
            continue
        dt_ = c.default_time(home)
        fiscal = c.fiscal_alias(home, dt_[0], dt_[1].name) if dt_ else None
        origin = (
            next(
                (
                    a
                    for a in reversed(c.em.ancestors(home.name))
                    if c.em.explores[a].base_view == home.base_view
                ),
                home.name,
            )
            if layer == "hub"
            else home.name
        )
        fields = [f"{home.base_alias if home.base_view == view else view}.{name}"]
        if fiscal:
            fields.append(f"{fiscal}.fiscal_quarter_label")
        out[layer].append(
            ExampleQuestion(
                question=f"Show {r.data['label']} by fiscal quarter",
                explore=origin,
                fields=fields,
            )
        )
    return out


def _check_contradictions(
    rules: list[tuple[str, InstructionRule]],
) -> list[tuple[str, InstructionRule]]:
    by_subject: dict[str, list[tuple[str, InstructionRule]]] = {}
    for layer, r in rules:
        for s in r.subjects:
            by_subject.setdefault(s, []).append((layer, r))
    drop: set[str] = set()
    errors: list[str] = []
    for subject, claims in sorted(by_subject.items()):
        if len(claims) < 2:
            continue
        hub_claims = [r for layer, r in claims if layer == "hub"]
        spoke_claims = [r for layer, r in claims if layer == "spoke"]
        for group in (hub_claims, spoke_claims):
            if len({r.assertion for r in group}) > 1:
                errors.append(
                    f"ambiguous: {subject!r} is claimed differently by "
                    + ", ".join(
                        f"{r.rule_id} ({r.provenance.project}/{r.provenance.file}:{r.provenance.line})"
                        for r in group
                    )
                )
        for hr in hub_claims:
            for sr in spoke_claims:
                if sr.assertion == hr.assertion:
                    drop.add(sr.rule_id)
                elif hr.certified:
                    errors.append(
                        f"contradiction: spoke rule {sr.rule_id} "
                        f"({sr.provenance.project}/{sr.provenance.file}:{sr.provenance.line}) contradicts "
                        f"hub certified rule {hr.rule_id} on {subject!r}"
                    )
    if errors:
        raise CompileError("\n".join(sorted(set(errors))))
    return [(layer, r) for layer, r in rules if not (layer == "spoke" and r.rule_id in drop)]


def compile_agent(
    *,
    hub: str,
    em: EffectiveModel,
    hub_model: EffectiveModel,
    catalog: Catalog,
) -> AgentInstructions:
    c = _Ctx(hub=hub, spoke=em.project, em=em, hub_model=hub_model, catalog=catalog)
    rules = [
        *_metric_rules(c),
        *_vocab_rules(c),
        *_explore_rules(c),
        *_fiscal_rules(c),
        *_pii_rules(c),
    ]
    rules = _check_contradictions(rules)
    glossary, guidance = _glossary_and_guidance(c)
    examples = _examples(c, rules)
    layers: list[Layer] = []
    for role, project in (("hub", hub), ("spoke", em.project)):
        layer = Layer(
            layer_id=f"{role}:{project}",
            project=project,
            role=role,  # type: ignore[arg-type]
            rules=sorted((r for lay, r in rules if lay == role), key=lambda r: r.rule_id),
            glossary=sorted(glossary[role], key=lambda g: g.term_id),
            field_guidance=sorted(guidance[role], key=lambda g: g.field),
            example_questions=sorted(examples[role], key=lambda q: (q.explore, q.question)),
        )
        layers.append(layer)
    explores: list[ExploreRef] = []
    for e in c.explores:
        dt_ = c.default_time(e)
        explores.append(
            ExploreRef(
                explore=e.name,
                label=e.label or e.name,
                description=_norm(e.description),
                default_time_field=f"{dt_[0]}.{dt_[1].name}" if dt_ else None,
                fiscal_alias=c.fiscal_alias(e, dt_[0], dt_[1].name) if dt_ else None,
                extends_chain=c.em.ancestors(e.name)[1:],
            )
        )
    model = em.model_files[0].rsplit("/", 1)[-1].removesuffix(".model.lkml")
    return AgentInstructions(
        agent_id=em.project, lookml_model=model, explores=explores, layers=layers
    )


def finalize(instr: AgentInstructions) -> AgentInstructions:
    for layer in instr.layers:
        layer.content_hash = layer.compute_hash()
    instr.content_hash = instr.compute_hash()
    return instr
