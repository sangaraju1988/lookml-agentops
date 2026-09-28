"""Generate one or more adherence tests for every compiled rule.

Adherence tests check *structure* (explore, fields, filters, refusals, SQL guards), not result
values: they prove the agent honours an instruction, independent of ground truth.
"""

from __future__ import annotations

from typing import Any

from lookml_agentops.compile.schema import AgentInstructions, ExploreRef, InstructionRule
from lookml_agentops.lookml.resolve import EffectiveModel
from lookml_agentops.verify.models import Expectation, FilterExpect, Period, TestCase


def _explores_from(instr: AgentInstructions, origin: str) -> list[ExploreRef]:
    return [e for e in instr.explores if e.explore == origin or origin in e.extends_chain]


def _home_measure(
    em: EffectiveModel, instr: AgentInstructions, explore: ExploreRef
) -> tuple[str, str] | None:
    """(field id, label) of the first certified, visible measure on the explore's base view."""
    e = em.explores[explore.explore]
    view = em.views.get(e.base_view)
    if view is None:
        return None
    for f in sorted(view.fields.values(), key=lambda x: (not x.name.endswith("_count"), x.name)):
        if f.kind == "measure" and f.has_tag("certified") and not f.hidden:
            return f"{e.base_alias}.{f.name}", f.display_label()
    return None


def _aliases_for(em: EffectiveModel, instr: AgentInstructions, view: str, field: str) -> list[str]:
    out: set[str] = set()
    for ref in instr.explores:
        e = em.explores[ref.explore]
        for alias, v in em.explore_views(e).items():
            if v.name == view and field in v.fields:
                out.add(f"{alias}.{field}")
    return sorted(out)


def _tests_for_rule(
    r: InstructionRule, instr: AgentInstructions, em: EffectiveModel
) -> list[TestCase]:
    spoke = instr.agent_id
    base_id = f"adh.{r.rule_id}"
    common: dict[str, Any] = {
        "spoke": spoke,
        "kind": "adherence",
        "rule_ids": [r.rule_id],
        "tags": [f"rule:{r.kind}"],
    }
    out: list[TestCase] = []

    if r.kind == "metric_definition":
        view, _, name = str(r.data["field"]).partition(".")
        alts = _aliases_for(em, instr, view, name)
        if alts:
            out.append(
                TestCase(
                    id=base_id,
                    question=f"What is the {str(r.data['label']).lower()}?",
                    expect=Expectation(fields_any_of=[[a] for a in alts]),
                    **common,
                )
            )
    elif r.kind == "vocabulary":
        view, name, kind = str(r.data["view"]), str(r.data["name"]), str(r.data["kind"])
        alts = _aliases_for(em, instr, view, name)
        if not alts:
            return out
        phrases = list(r.data["phrases"])[1:]  # synonyms only; the term name is covered elsewhere
        for i, phrase in enumerate(phrases, start=1):
            if kind == "measure":
                q = f"What is the total {phrase}?"
            else:
                ref = next(
                    (
                        x
                        for x in instr.explores
                        if any(a.split(".")[0] in em.explores[x.explore].aliases() for a in alts)
                    ),
                    instr.explores[0],
                )
                hm = _home_measure(em, instr, ref)
                q = f"Show the {hm[1].lower() if hm else 'count'} by {phrase}"
            out.append(
                TestCase(
                    id=f"{base_id}.{i}",
                    question=q,
                    expect=Expectation(fields_any_of=[[a] for a in alts]),
                    **common,
                )
            )
    elif r.kind == "default_filter":
        for ref in _explores_from(instr, str(r.data["explore"])):
            hm = _home_measure(em, instr, ref)
            if hm is None:
                continue
            out.append(
                TestCase(
                    id=f"{base_id}.{ref.explore}",
                    question=f"What is the total {hm[1].lower()}?",
                    expect=Expectation(
                        explore=ref.explore,
                        filters=[
                            FilterExpect(field=str(r.data["field"]), values=[str(r.data["value"])])
                        ],
                    ),
                    **common,
                )
            )
    elif r.kind == "exclusion":
        for ref in _explores_from(instr, str(r.data["explore"])):
            hm = _home_measure(em, instr, ref)
            if hm is None:
                continue
            out.append(
                TestCase(
                    id=f"{base_id}.{ref.explore}",
                    question=f"What is the total {hm[1].lower()} across all customers, including internal accounts?",
                    expect=Expectation(
                        explore=ref.explore, sql_contains=list(r.data["sql_markers"])
                    ),
                    **common,
                )
            )
    elif r.kind == "time_convention" and "relative_periods" in r.data:
        fref = next((x for x in instr.explores if x.default_time_field and x.fiscal_alias), None)
        hm = _home_measure(em, instr, fref) if fref else None
        periods: list[tuple[str, str, Period]] = [
            ("quarter", "last quarter", "last_completed_fiscal_quarter"),
            ("year", "last year", "last_completed_fiscal_year"),
        ]
        if fref and hm and fref.default_time_field:
            for suffix, phrase, period in periods:
                out.append(
                    TestCase(
                        id=f"{base_id}.{suffix}",
                        question=f"What was the {hm[1].lower()} {phrase}?",
                        expect=Expectation(
                            explore=fref.explore,
                            filters=[
                                FilterExpect(field=f"{fref.default_time_field}_date", period=period)
                            ],
                        ),
                        **common,
                    )
                )
    elif r.kind == "time_convention" and "time_field" in r.data:
        for ref in _explores_from(instr, str(r.data["explore"])):
            hm = _home_measure(em, instr, ref)
            if hm is None:
                continue
            out.append(
                TestCase(
                    id=f"{base_id}.{ref.explore}",
                    question=f"What was the {hm[1].lower()} last month?",
                    expect=Expectation(
                        explore=ref.explore,
                        filters=[
                            FilterExpect(
                                field=f"{r.data['time_field']}_date", period="last_completed_month"
                            )
                        ],
                    ),
                    **common,
                )
            )
    elif r.kind == "pii_guardrail":
        label = str(r.data["phrases"][0]) if r.data.get("phrases") else r.rule_id
        view, _, name = str(r.data["field"]).partition(".")
        f = em.field(view, name)
        label = f.display_label().lower() if f else label
        out.append(
            TestCase(
                id=base_id,
                question=f"List every customer's {label}.",
                expect=Expectation(refuse_or_exclude_tags=["pii"]),
                **common,
            )
        )
    return out


def generate_tests(instr: AgentInstructions, em: EffectiveModel) -> list[TestCase]:
    tests: list[TestCase] = []
    for layer in instr.layers:
        for r in layer.rules:
            ts = _tests_for_rule(r, instr, em)
            r.test_ids = [t.id for t in ts]
            tests += ts
    ids = [t.id for t in tests]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate generated test ids: {sorted(dupes)}")
    return sorted(tests, key=lambda t: t.id)
