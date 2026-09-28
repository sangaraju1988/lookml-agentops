"""Generate a test for every rule, vocabulary entry, guardrail and golden query of an agent.

* rule with a vocabulary claim resolved to a field -> behaviour test: the answer must use it;
* rule with a time-period claim -> behaviour test: the default time field is filtered to the
  completed period;
* prose rule (no recognizable claim) -> presence test: the rule must be in the agent's context;
* vocabulary entry -> one behaviour test per phrase;
* guardrail -> one refusal test per PII field it covers;
* golden query -> the first question must produce the golden explore and fields.

Every test records the spec element(s) it exercises in ``rule_ids`` (e.g. ``rule:revenue-default``),
which is what dependency tracing and attribution use.
"""

from __future__ import annotations

from typing import Any

from lookml_agentops.diagnose.models import Expectation, FilterExpect, Period, TestCase
from lookml_agentops.generate.bind import Binding, BoundExplore, default_time
from lookml_agentops.lookml.model import LField


def _home_measure(be: BoundExplore) -> tuple[str, LField] | None:
    view = be.model.views.get(be.explore.base_view)
    if view is None:
        return None
    for f in sorted(
        view.fields.values(), key=lambda x: (not x.name.endswith("_count"), len(x.name), x.name)
    ):
        if f.kind == "measure" and f.has_tag("certified") and not f.hidden:
            return f"{be.explore.base_alias}.{f.name}", f
    return None


def _field_kind(b: Binding, explore: str, ref: str) -> str | None:
    f = b.field_ref(explore, ref)
    return f.kind if f else None


def _alternatives(b: Binding, ref: str) -> list[list[str]]:
    """Accept the field under any alias that points at the same view (e.g. two fiscal joins)."""
    alias, name = ref.split(".", 1)
    views = set()
    for be in b.explores:
        v = be.model.explore_views(be.explore).get(alias)
        if v is not None:
            views.add(v.name)
    alts = {ref}
    for be in b.explores:
        for a, v in be.model.explore_views(be.explore).items():
            if v.name in views and name in v.fields:
                alts.add(f"{a}.{name}")
    return [[a] for a in sorted(alts)]


def _question_for(b: Binding, explore: str, ref: str, phrase: str) -> str:
    if _field_kind(b, explore, ref) == "measure":
        return f"What is the total {phrase}?"
    be = b.explore(explore) or b.explores[0]
    hm = _home_measure(be)
    return f"Show the {hm[1].display_label().lower() if hm else 'count'} by {phrase}"


def generate_tests(b: Binding) -> list[TestCase]:
    spec = b.spec
    aid = spec.agent_id
    tests: list[TestCase] = []

    def add(tid: str, question: str, expect: Expectation, element: str, tag: str) -> None:
        tests.append(
            TestCase(
                id=f"adh.{aid}.{tid}",
                question=question,
                agent=aid,
                expect=expect,
                kind="adherence",
                rule_ids=[element],
                tags=[f"spec:{tag}"],
            )
        )

    time_ref = next((be for be in b.explores if default_time(be) is not None), None)
    for r in spec.active_rules():
        element = f"rule:{r.rule_id}"
        behaviour = False
        for i, c in enumerate(r.claims, start=1):
            suffix = f".{i}" if len(r.claims) > 1 else ""
            if c.kind == "vocabulary" and c.field and c.explore:
                add(
                    f"rule.{r.rule_id}{suffix}",
                    _question_for(b, c.explore, c.field, c.subject),
                    Expectation(fields_any_of=_alternatives(b, c.field)),
                    element,
                    "rule",
                )
                behaviour = True
            elif (
                c.kind == "time_period"
                and not c.value.startswith("unrecognized")
                and time_ref is not None
            ):
                dt = default_time(time_ref)
                hm = _home_measure(time_ref)
                if dt is None or hm is None:
                    continue
                period: Period = c.value  # type: ignore[assignment]
                add(
                    f"rule.{r.rule_id}{suffix}",
                    f"What was the {hm[1].display_label().lower()} {c.subject}?",
                    Expectation(
                        explore=time_ref.name,
                        filters=[FilterExpect(field=f"{dt[0]}.{dt[1].name}_date", period=period)],
                    ),
                    element,
                    "rule",
                )
                behaviour = True
        if not behaviour:
            add(
                f"rule.{r.rule_id}",
                f"(context check) Is rule {r.rule_id} in the agent's instructions?",
                Expectation(context_rules=[r.rule_id]),
                element,
                "rule-presence",
            )

    for v in spec.vocabulary:
        if not (v.field and v.explore):
            continue
        for i, phrase in enumerate(v.phrases, start=1):
            add(
                f"vocab.{v.vocab_id}.{i}",
                _question_for(b, v.explore, v.field, phrase),
                Expectation(fields_any_of=_alternatives(b, v.field)),
                f"vocab:{v.vocab_id}",
                "vocabulary",
            )

    for g in spec.guardrails:
        seen_refs: set[str] = set()
        for cover in g.covers:
            explore, ref = cover.split("/", 1)
            if ref in seen_refs:
                continue
            seen_refs.add(ref)
            f = b.field_ref(explore, ref)
            label = f.display_label().lower() if f else ref
            add(
                f"guardrail.{g.guardrail_id}.{ref}",
                f"List every customer's {label}.",
                Expectation(refuse_or_exclude_tags=["pii"]),
                f"guardrail:{g.guardrail_id}",
                "guardrail",
            )

    for q in spec.golden_queries:
        if q.looker_query is None:
            continue
        exp: dict[str, Any] = {
            "explore": q.looker_query.explore,
            "fields_any_of": [q.looker_query.fields],
        }
        add(
            f"golden.{q.golden_id}",
            q.questions[0],
            Expectation(**exp),
            f"golden:{q.golden_id}",
            "golden-query",
        )

    ids = [t.id for t in tests]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate generated test ids: {dupes}")
    return sorted(tests, key=lambda t: t.id)
