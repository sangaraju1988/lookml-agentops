"""Element snapshots: fine-grained fingerprints of everything a test can depend on.

An *element* is a LookML field or explore (per effective model), a spec rule / vocabulary entry /
guardrail / golden query, a catalog term, a test definition, the runner, or one ground-truth
result. Each element has *facets*; each facet has a version and the input + location that last
set it:

* LookML fields: ``semantic`` (sql, type, filters, primary_key, datatype, timeframes) and
  ``surface`` (label, description, group_label, tags, hidden, value_format_name, ...);
* LookML explores: ``semantic`` (sql_always_where, always_filter, joins, view_name/from) and
  ``surface`` (label, description, tags, hidden);
* everything else: a single ``content`` facet.

Per-parameter locations come from the resolver's ``param_locs``, so a SQL change made in an
imported project is attributed to that project even if an importing project refined the field's
wording later.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from lookml_agentops._util.hashing import sha256_obj
from lookml_agentops.catalog import Catalog
from lookml_agentops.lookml.model import LExplore, LField, Loc
from lookml_agentops.lookml.resolve import EffectiveModel
from lookml_agentops.spec.model import AgentSpec

FIELD_SEMANTIC = ("sql", "type", "filters", "primary_key", "datatype", "timeframes", "convert_tz")
EXPLORE_SEMANTIC = ("sql_always_where", "always_filter", "view_name", "from", "extends")
SURFACE_EXCLUDE = {"extension"}


class Facet(BaseModel):
    version: str
    input_id: str
    location: str | None = None  # project-relative "file:line" of the last parameter set
    params: dict[str, Any] = Field(
        default_factory=dict
    )  # param -> {"v": value hash/text, "loc": ...}


class Element(BaseModel):
    element_id: str
    kind: str  # field | explore | rule | vocab | guardrail | golden | term | test | runner | gt | spec
    facets: dict[str, Facet]

    def input_ids(self) -> set[str]:
        return {f.input_id for f in self.facets.values()}


def _short(v: Any) -> Any:
    """Keep small scalars readable in diffs; hash anything large."""
    text = v if isinstance(v, str) else repr(v)
    return text if len(text) <= 160 else f"sha:{sha256_obj(v)[:12]}"


def _facet(params: dict[str, Any], locs: dict[str, Loc], fallback: Loc) -> Facet:
    snap = {k: {"v": _short(params[k]), "loc": str(locs.get(k, fallback))} for k in sorted(params)}
    last = max(
        (locs.get(k, fallback) for k in params),
        default=fallback,
        key=lambda loc: (loc.project, loc.file, loc.line),
    )
    # attribute the facet to the project that set its most "important" parameter
    lead = next(
        (locs[k] for k in ("sql", "sql_always_where", "type") if k in params and k in locs), last
    )
    return Facet(
        version=sha256_obj({k: params[k] for k in sorted(params)})[:16],
        input_id=f"lookml:{lead.project}",
        location=f"{lead.file}:{lead.line}",
        params=snap,
    )


def field_element(scope: str, f: LField) -> Element:
    sem = {k: v for k, v in f.params.items() if k in FIELD_SEMANTIC}
    sur = {
        k: v for k, v in f.params.items() if k not in FIELD_SEMANTIC and k not in SURFACE_EXCLUDE
    }
    fb = f.last_at
    return Element(
        element_id=f"field:{scope}/{f.view}.{f.name}",
        kind="field",
        facets={
            "semantic": _facet(sem, f.param_locs, f.defined_at),
            "surface": _facet(sur, f.param_locs, fb),
        },
    )


def explore_element(scope: str, e: LExplore) -> Element:
    sem = {k: v for k, v in e.params.items() if k in EXPLORE_SEMANTIC}
    sem["joins"] = {j.name: {k: v for k, v in sorted(j.params.items())} for j in e.joins.values()}
    locs = dict(e.param_locs)
    if e.joins:
        locs["joins"] = max(
            (j.provenance[-1].loc for j in e.joins.values()),
            key=lambda loc: (loc.project, loc.file, loc.line),
        )
    sur = {
        k: v for k, v in e.params.items() if k not in EXPLORE_SEMANTIC and k not in SURFACE_EXCLUDE
    }
    fb = e.provenance[-1].loc
    return Element(
        element_id=f"explore:{scope}/{e.name}",
        kind="explore",
        facets={"semantic": _facet(sem, locs, fb), "surface": _facet(sur, locs, fb)},
    )


def model_elements(models: dict[str, EffectiveModel]) -> dict[str, Element]:
    out: dict[str, Element] = {}
    for scope, em in sorted(models.items()):
        for v in em.views.values():
            for f in v.fields.values():
                el = field_element(scope, f)
                out[el.element_id] = el
        for e in em.explores.values():
            el = explore_element(scope, e)
            out[el.element_id] = el
    return out


def _content(
    element_id: str, kind: str, input_id: str, payload: Any, location: str | None
) -> Element:
    return Element(
        element_id=element_id,
        kind=kind,
        facets={
            "content": Facet(
                version=sha256_obj(payload)[:16],
                input_id=input_id,
                location=location,
                params={"value": {"v": _short(payload), "loc": location or ""}},
            )
        },
    )


def spec_elements(spec: AgentSpec) -> dict[str, Element]:
    out: dict[str, Element] = {}
    for r in spec.rules:
        out[f"rule:{r.rule_id}"] = _content(
            f"rule:{r.rule_id}",
            "rule",
            f"agent:{r.origin_agent}",
            [r.text, r.locked, r.overridden_by],
            str(r.source),
        )
    for v in spec.vocabulary:
        out[f"vocab:{v.vocab_id}"] = _content(
            f"vocab:{v.vocab_id}",
            "vocab",
            f"agent:{v.origin_agent}",
            [v.phrases, v.target],
            str(v.source),
        )
    for g in spec.guardrails:
        out[f"guardrail:{g.guardrail_id}"] = _content(
            f"guardrail:{g.guardrail_id}",
            "guardrail",
            f"agent:{g.origin_agent}",
            g.text,
            str(g.source),
        )
    for q in spec.golden_queries:
        payload = [
            q.questions,
            q.looker_query.model_dump(mode="json") if q.looker_query else q.explore_url,
        ]
        out[f"golden:{q.golden_id}"] = _content(
            f"golden:{q.golden_id}", "golden", f"agent:{q.origin_agent}", payload, str(q.source)
        )
    ctx = {
        k: v
        for k, v in spec.model_dump(mode="json").items()
        if k in ("role", "audience", "description", "explores", "derive")
    }
    out[f"spec:{spec.agent_id}"] = _content(
        f"spec:{spec.agent_id}", "spec", f"agent:{spec.agent_id}", ctx, str(spec.source)
    )
    return out


def catalog_elements(catalog: Catalog, name: str, rel: str) -> dict[str, Element]:
    out: dict[str, Element] = {}
    for t in catalog.terms:
        loc = f"{rel}:{catalog.lines.get(t.id, 1)}"
        out[f"term:{t.id}"] = _content(
            f"term:{t.id}", "term", f"catalog:{name}", t.model_dump(mode="json"), loc
        )
    return out
