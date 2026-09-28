"""MockRunner: a deterministic, rule-based stand-in for a Looker AI agent.

It reads the compiled ``agent_spec.v1`` exactly as a real agent reads its context:

* **vocabulary** — spec vocabulary entries, then rule claims (``"Revenue" means net revenue``),
  then derived glossary terms, then LookML labels;
* **time conventions** — rule claims such as ``"Last quarter" means the last completed fiscal
  quarter``; without one, relative periods fall back to *calendar* periods, as a naive agent would;
* **guardrails** — a question touching PII a guardrail covers is refused; uncovered PII leaks;
* **explores** — only the spec's explores, preferring the one whose base view owns the measure.

It then compiles SQL from the effective LookML model and runs it on the local DuckDB seed.
Changing the spec therefore changes the mock's answers.

External scenarios simulate vendor/runtime behaviour changes (nothing on the team's side changes):

* ``baseline``                 — reference behaviour;
* ``fuzzy_values``             — filter values resolved by fuzzy matching (``Shipped`` also
  matches ``Shipped - Partial``; ``Northstar Freight`` also matches ``North Star Freight LLC``);
* ``instruction_override``     — a vendor system instruction maps bare "revenue"/"sales" to gross
  revenue, overriding the team's rules;
* ``explore_selection_shift``  — when several explores could answer, the vendor picks the least
  specific one.
"""

from __future__ import annotations

import datetime as dt
import difflib
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from lookml_agentops.diagnose.models import AgentAnswer, ResolvedFilter, RunnerMeta, TestCase
from lookml_agentops.diagnose.runners.base import AgentContext, Runner
from lookml_agentops.generate.bind import BoundExplore, preferred_alias
from lookml_agentops.lookml.model import LExplore
from lookml_agentops.lookml.sqlgen import QueryFilter, SqlBuilder, SqlGenError
from lookml_agentops.seed import fiscal
from lookml_agentops.spec.interpret import PII_KINDS, normalize

MOCK_VERSION = "mock-2.0"


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    fuzzy_values: bool = False
    phrase_overrides: tuple[tuple[str, str], ...] = ()  # phrase -> view.field
    worst_explore: bool = False


SCENARIOS: dict[str, Scenario] = {
    "baseline": Scenario("baseline", "reference behaviour"),
    "fuzzy_values": Scenario(
        "fuzzy_values",
        "filter values are resolved by fuzzy matching against sampled dimension values",
        fuzzy_values=True,
    ),
    "instruction_override": Scenario(
        "instruction_override",
        "a vendor system instruction maps 'revenue'/'sales' to gross revenue, overriding team rules",
        phrase_overrides=(("revenue", "orders.gross_revenue"), ("sales", "orders.gross_revenue")),
    ),
    "explore_selection_shift": Scenario(
        "explore_selection_shift",
        "when several explores could answer, the least specific one is chosen",
        worst_explore=True,
    ),
}

CALENDAR_FALLBACK = {
    "last quarter": "calendar_quarter",
    "last year": "calendar_year",
    "last month": "last_completed_month",
}
TIMEFRAME_PHRASES = [
    ("by month", "month"),
    ("per month", "month"),
    ("monthly", "month"),
    ("by week", "week"),
    ("weekly", "week"),
    ("by day", "date"),
    ("daily", "date"),
    ("by date", "date"),
]
QUOTE_RE = re.compile(r'"([^"]+)"')
FYQ_RE = re.compile(r"\bfy\s?(\d{4})\s*-?\s*q([1-4])\b")
FY_RE = re.compile(r"\bfy\s?(\d{4})\b")


class MockError(Exception):
    pass


@dataclass
class _Match:
    start: int
    end: int
    view: str
    name: str
    kind: str
    element: str | None


@dataclass
class _Parse:
    measures: list[_Match] = field(default_factory=list)
    dims: list[_Match] = field(default_factory=list)
    timeframe: str | None = None
    period: tuple[dt.date, dt.date, str] | None = None
    quoted: list[tuple[str, _Match | None]] = field(default_factory=list)
    elements: list[str] = field(default_factory=list)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _json_value(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dt.datetime | dt.date):
        return v.isoformat()
    return v


def _add_months(d: dt.date, n: int) -> dt.date:
    i = d.year * 12 + d.month - 1 + n
    return dt.date(i // 12, i % 12 + 1, 1)


def calendar_period(kind: str, as_of: dt.date) -> tuple[dt.date, dt.date, str]:
    if kind == "calendar_quarter":
        cur = dt.date(as_of.year, 3 * ((as_of.month - 1) // 3) + 1, 1)
        prev = _add_months(cur, -3)
        return prev, cur, f"{prev.year}-Q{(prev.month - 1) // 3 + 1}"
    if kind == "calendar_year":
        return dt.date(as_of.year - 1, 1, 1), dt.date(as_of.year, 1, 1), str(as_of.year - 1)
    return fiscal.last_completed_month(as_of)


def fiscal_period(kind: str, as_of: dt.date) -> tuple[dt.date, dt.date, str]:
    if kind == "last_completed_fiscal_quarter":
        return fiscal.last_completed_fiscal_quarter(as_of)
    if kind == "last_completed_fiscal_year":
        return fiscal.last_completed_fiscal_year(as_of)
    if kind == "last_completed_month":
        return fiscal.last_completed_month(as_of)
    return calendar_period(kind, as_of)


class MockRunner(Runner):
    def __init__(self, scenario: str = "baseline") -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown mock scenario {scenario!r}; choose from {sorted(SCENARIOS)}")
        self.scenario = SCENARIOS[scenario]
        self._distinct_cache: dict[tuple[str, str, str], list[str]] = {}

    @property
    def meta(self) -> RunnerMeta:
        return RunnerMeta(
            runner="mock",
            runner_version=f"{MOCK_VERSION}/{self.scenario.name}",
            vendor_profile=self.scenario.name,
            structured=True,
        )

    def answer(self, test: TestCase, ctx: AgentContext) -> AgentAnswer:
        context = [r.rule_id for r in ctx.spec.active_rules()]
        if (
            test.expect.context_rules
            and not test.expect.fields_any_of
            and test.question.startswith("(context")
        ):
            return AgentAnswer(meta=self.meta, context_rules=context)
        try:
            ans = self._answer(test.question, ctx)
        except (MockError, SqlGenError) as exc:
            ans = AgentAnswer(meta=self.meta, error=str(exc))
        return ans.model_copy(update={"context_rules": context})

    # ---- phrase table ----------------------------------------------------------------------
    def _phrase_table(
        self, ctx: AgentContext, *, include_pii: bool
    ) -> dict[str, tuple[str, str, str | None]]:
        """phrase -> (view, field, spec element id). Earlier sources win."""
        spec, b = ctx.spec, ctx.binding
        table: dict[str, tuple[str, str, str | None]] = {}

        def view_field(explore: str | None, ref: str | None) -> tuple[str, str] | None:
            if not explore or not ref:
                return None
            f = b.field_ref(explore, ref)
            return (f.view, f.name) if f else None

        for phrase, target in self.scenario.phrase_overrides:
            view, _, name = target.partition(".")
            if any(be.model.explore_views(be.explore).get(view) for be in b.explores):
                table[phrase] = (view, name, "external:instruction_override")
        for v in spec.vocabulary:
            vf = view_field(v.explore, v.field)
            if vf:
                for p in v.phrases:
                    table.setdefault(normalize(p), (*vf, f"vocab:{v.vocab_id}"))
        for r in spec.active_rules():
            for c in r.claims:
                vf = view_field(c.explore, c.field) if c.kind == "vocabulary" else None
                if vf:
                    table.setdefault(c.subject, (*vf, f"rule:{r.rule_id}"))
        if spec.derived:
            for t in spec.derived.glossary:
                for be in b.explores:
                    vf = view_field(be.name, t.field)
                    if vf:
                        for p in [t.term, *t.synonyms]:
                            table.setdefault(normalize(p), (*vf, None))
                        break
        for be in b.explores:
            for lview in be.model.explore_views(be.explore).values():
                for f in lview.fields.values():
                    if f.kind == "dimension_group" or (
                        f.hidden and not (include_pii and f.has_tag("pii"))
                    ):
                        continue
                    if f.has_tag("ai_hidden") and not include_pii:
                        continue
                    if f.has_tag("pii") and not include_pii:
                        continue
                    table.setdefault(normalize(f.display_label()), (lview.name, f.name, None))
        return table

    # ---- parsing ---------------------------------------------------------------------------
    def _parse(self, question: str, ctx: AgentContext) -> _Parse | AgentAnswer:
        spec = ctx.spec
        quotes = QUOTE_RE.findall(question)
        text = question.lower()
        for i, _ in enumerate(quotes):
            text = QUOTE_RE.sub(f" qv{i}qv ", text, count=1)
        text = re.sub(r"[?!,.;:']", " ", text)
        text = " " + re.sub(r"\s+", " ", text).strip() + " "
        out = _Parse()

        # guardrails: refuse questions about PII a guardrail covers
        asked_kinds = {k for k, words in PII_KINDS.items() if any(f" {w} " in text for w in words)}
        for g in spec.guardrails:
            if asked_kinds & set(g.pii_kinds):
                return AgentAnswer(
                    refused=True,
                    refusal_reason=f"guardrail {g.guardrail_id}",
                    applied_rules=[f"guardrail:{g.guardrail_id}"],
                    meta=self.meta,
                )

        # relative periods: the spec's time conventions, else calendar periods
        claims = {
            c.subject: (c.value, r.rule_id)
            for r in spec.active_rules()
            for c in r.claims
            if c.kind == "time_period" and not c.value.startswith("unrecognized")
        }
        for phrase in sorted(set(claims) | set(CALENDAR_FALLBACK), key=len, reverse=True):
            if f" {phrase} " in text:
                if phrase in claims:
                    kind, rid = claims[phrase]
                    out.period = fiscal_period(kind, ctx.as_of)
                    out.elements.append(f"rule:{rid}")
                else:
                    out.period = calendar_period(CALENDAR_FALLBACK[phrase], ctx.as_of)
                text = text.replace(f" {phrase} ", " ", 1)
                break
        if out.period is None:
            if fm := FYQ_RE.search(text):
                out.period = fiscal.fiscal_quarter_range(int(fm.group(1)), int(fm.group(2)))
                text = text[: fm.start()] + " " + text[fm.end() :]
            elif fm := FY_RE.search(text):
                out.period = fiscal.fiscal_year_range(int(fm.group(1)))
                text = text[: fm.start()] + " " + text[fm.end() :]
        for phrase, tf in TIMEFRAME_PHRASES:
            if f" {phrase} " in text:
                out.timeframe = tf
                text = text.replace(f" {phrase} ", " ", 1)
                break

        table = self._phrase_table(ctx, include_pii=bool(asked_kinds))
        taken: list[tuple[int, int]] = []
        matches: list[_Match] = []
        for phrase in sorted(table, key=lambda p: (-len(p), p)):
            for pm in re.finditer(rf"(?<![\w-]){re.escape(phrase)}(?![\w-])", text):
                s, e = pm.start(), pm.end()
                if any(s < te and e > ts for ts, te in taken):
                    continue
                view, name, element = table[phrase]
                f = next(
                    (
                        be.model.views[view].fields.get(name)
                        for be in ctx.binding.explores
                        if view in be.model.views and name in be.model.views[view].fields
                    ),
                    None,
                )
                if f is None:
                    continue
                taken.append((s, e))
                matches.append(_Match(s, e, view, name, f.kind, element))
        matches.sort(key=lambda m: m.start)

        targets: dict[int, _Match] = {}
        for i, _ in enumerate(quotes):
            pos = text.find(f"qv{i}qv")
            prev = [m for m in matches if m.kind != "measure" and m.end <= pos]
            if prev and all(
                w in ("is", "of", "was", "were", "=", "had", "has", "for")
                for w in text[prev[-1].end : pos].split()
            ):
                targets[i] = prev[-1]
        target_ids = {id(m) for m in targets.values()}
        seen: set[tuple[str, str]] = set()
        for m in matches:
            if (m.view, m.name) in seen:
                continue
            seen.add((m.view, m.name))
            if m.element:
                out.elements.append(m.element)
            if m.kind == "measure":
                out.measures.append(m)
            elif id(m) not in target_ids:
                out.dims.append(m)
        out.quoted = [(q, targets.get(i)) for i, q in enumerate(quotes)]
        return out

    # ---- explore selection -----------------------------------------------------------------
    @staticmethod
    def _distances(e: LExplore) -> dict[str, int]:
        dist = {e.base_alias: 0}
        changed = True
        while changed:
            changed = False
            for j in e.joins.values():
                refs = {a for a in re.findall(r"\$\{(\w+)\.", j.sql_on or "") if a != j.name}
                known = [dist[a] for a in refs if a in dist]
                if known and len(known) == len(refs):
                    d = max(known) + 1
                    if dist.get(j.name, 1 << 30) > d:
                        dist[j.name] = d
                        changed = True
        return dist

    def _choose(self, parse: _Parse, ctx: AgentContext) -> tuple[BoundExplore, dict[str, int]]:
        needed = [(m.view, m.name) for m in [*parse.measures, *parse.dims]]
        needed += [(m.view, m.name) for _, m in parse.quoted if m is not None]
        cands: list[tuple[tuple[int, int, str], BoundExplore, dict[str, int]]] = []
        for be in ctx.binding.explores:
            views = be.model.explore_views(be.explore)
            dist = self._distances(be.explore)
            cost, ok = 0, True
            for view, _ in needed:
                ds = [dist.get(a, 99) for a, v in views.items() if v.name == view]
                if not ds:
                    ok = False
                    break
                cost += min(ds)
            if ok:
                first = (
                    parse.measures[0].view if parse.measures else (needed[0][0] if needed else "")
                )
                cands.append(((0 if be.explore.base_view == first else 1, cost, be.name), be, dist))
        if not cands:
            raise MockError(
                f"no explore of this agent contains all of {sorted({f'{v}.{n}' for v, n in needed})}"
            )
        cands.sort(key=lambda c: c[0])
        pick = cands[-1] if self.scenario.worst_explore else cands[0]
        return pick[1], pick[2]

    def _alias(self, view: str, be: BoundExplore, dist: dict[str, int]) -> str:
        alias = preferred_alias(be, view)
        if alias is None:
            raise MockError(f"view {view} is not joined in explore {be.name}")
        return alias

    # ---- value resolution ------------------------------------------------------------------
    def _distinct(self, ctx: AgentContext, e: BoundExplore, fld: str) -> list[str]:
        key = (ctx.agent_id, e.name, fld)
        if key not in self._distinct_cache:
            if ctx.con is None:
                raise MockError("mock runner needs a DuckDB connection")
            sql, _ = SqlBuilder(e.model, e.explore).build([fld], [])
            self._distinct_cache[key] = [
                str(r[0]) for r in ctx.con.execute(sql).fetchall() if r[0] is not None
            ]
        return self._distinct_cache[key]

    def _resolve_values(self, raw: str, candidates: list[str]) -> list[str]:
        if not self.scenario.fuzzy_values:
            return [v for v in candidates if v.strip().lower() == raw.strip().lower()]
        q = _norm(raw)
        return sorted(
            v
            for v in candidates
            if _norm(v).startswith(q)
            or q.startswith(_norm(v))
            or difflib.SequenceMatcher(None, q, _norm(v)).ratio() >= 0.85
        )

    def _value_filter(
        self,
        raw: str,
        target: _Match | None,
        be: BoundExplore,
        dist: dict[str, int],
        ctx: AgentContext,
    ) -> ResolvedFilter:
        views = be.model.explore_views(be.explore)
        if target is not None:
            cands = [f"{self._alias(target.view, be, dist)}.{target.name}"]
        else:
            cands = [
                f"{alias}.{f.name}"
                for alias in sorted(views, key=lambda a: (dist.get(a, 99), a))
                for f in views[alias].fields.values()
                if f.kind == "dimension"
                and f.type == "string"
                and not f.hidden
                and not f.has_tag("pii")
                and not f.has_tag("ai_hidden")
            ]
        for fld in cands:
            vals = self._resolve_values(raw, self._distinct(ctx, be, fld))
            if vals:
                return ResolvedFilter(field=fld, raw=raw, values=vals)
        raise MockError(f"could not resolve filter value {raw!r}")

    # ---- answer ----------------------------------------------------------------------------
    def _answer(self, question: str, ctx: AgentContext) -> AgentAnswer:
        parsed = self._parse(question, ctx)
        if isinstance(parsed, AgentAnswer):
            return parsed
        if not parsed.measures and not parsed.dims:
            raise MockError("no field recognized in question")
        be, dist = self._choose(parsed, ctx)
        ref = next(e for e in ctx.spec.explores if e.explore == be.name)
        dims = [f"{self._alias(m.view, be, dist)}.{m.name}" for m in parsed.dims]
        if parsed.timeframe:
            if not ref.default_time_field:
                raise MockError(f"explore {be.name} has no default time field")
            dims.append(f"{ref.default_time_field}_{parsed.timeframe}")
        measures = [f"{self._alias(m.view, be, dist)}.{m.name}" for m in parsed.measures]
        filters: list[ResolvedFilter] = [
            ResolvedFilter(field=k, raw=v, values=[v])
            for k, v in sorted(be.explore.always_filter.items())
        ]
        if parsed.period is not None:
            if not ref.default_time_field:
                raise MockError(f"explore {be.name} has no default time field")
            start, end, label = parsed.period
            filters.append(
                ResolvedFilter(
                    field=f"{ref.default_time_field}_date", raw=label, start=start, end=end
                )
            )
        for raw, target in parsed.quoted:
            filters.append(self._value_filter(raw, target, be, dist, ctx))
        qf = [
            QueryFilter(field=f.field, values=f.values, start=f.start, end=f.end) for f in filters
        ]
        sql, cols = SqlBuilder(be.model, be.explore).build([*dims, *measures], qf)
        if ctx.con is None:
            raise MockError("mock runner needs a DuckDB connection")
        rows = [[_json_value(v) for v in row] for row in ctx.con.execute(sql).fetchall()]
        return AgentAnswer(
            explore=be.name,
            fields=cols,
            filters=filters,
            sql=sql,
            rows=rows,
            applied_rules=sorted(set(parsed.elements)),
            meta=self.meta,
        )
