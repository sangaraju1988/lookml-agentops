"""MockRunner: a deterministic, rule-based stand-in for a Looker AI agent.

It reads the *compiled* instructions (vocabulary, time conventions, default filters, PII
guardrails) plus the effective model, picks an explore/fields/filters, compiles SQL with
:mod:`lookml_agentops.lookml.sqlgen` and runs it on the local DuckDB seed. Vendor profiles
simulate changes in vendor behaviour so drift can be demonstrated without Google access:

* ``v1`` — baseline: exact (case/whitespace-insensitive) filter-value resolution.
* ``v2_fuzzy_values`` — filter values resolved by fuzzy matching (``Shipped`` also matches
  ``Shipped - Partial``; ``Northstar Freight`` also matches ``North Star Freight LLC``).
* ``v3_instruction_override`` — a simulated vendor system instruction maps bare "revenue"/"sales"
  to gross revenue, overriding the team's vocabulary rule.
"""

from __future__ import annotations

import datetime as dt
import difflib
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from lookml_agentops.compile.schema import ExploreRef, InstructionRule
from lookml_agentops.lookml.model import LExplore
from lookml_agentops.lookml.sqlgen import QueryFilter, SqlBuilder, SqlGenError
from lookml_agentops.seed import fiscal
from lookml_agentops.verify.models import AgentAnswer, ResolvedFilter, RunnerMeta, TestCase
from lookml_agentops.verify.runners.base import Runner, SpokeContext

MOCK_VERSION = "mock-1.0"


@dataclass(frozen=True)
class VendorProfile:
    name: str
    description: str
    fuzzy_values: bool = False
    phrase_overrides: tuple[tuple[str, str], ...] = ()


PROFILES: dict[str, VendorProfile] = {
    "v1": VendorProfile("v1", "baseline behaviour"),
    "v2_fuzzy_values": VendorProfile(
        "v2_fuzzy_values",
        "filter values are resolved by fuzzy matching against sampled dimension values",
        fuzzy_values=True,
    ),
    "v3_instruction_override": VendorProfile(
        "v3_instruction_override",
        "a vendor system instruction maps 'revenue'/'sales' to gross revenue, overriding team rules",
        phrase_overrides=(("revenue", "orders.gross_revenue"), ("sales", "orders.gross_revenue")),
    ),
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
    rule_id: str | None


@dataclass
class _Parse:
    measures: list[_Match] = field(default_factory=list)
    dims: list[_Match] = field(default_factory=list)
    timeframe: str | None = None
    period: tuple[dt.date, dt.date, str] | None = None
    quoted: list[tuple[str, _Match | None]] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _json_value(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dt.datetime | dt.date):
        return v.isoformat()
    return v


class MockRunner(Runner):
    def __init__(self, profile: str = "v1") -> None:
        if profile not in PROFILES:
            raise ValueError(f"unknown vendor profile {profile!r}; choose from {sorted(PROFILES)}")
        self.profile = PROFILES[profile]
        self._distinct_cache: dict[tuple[str, str, str], list[str]] = {}

    @property
    def meta(self) -> RunnerMeta:
        return RunnerMeta(
            runner="mock",
            runner_version=f"{MOCK_VERSION}/{self.profile.name}",
            vendor_profile=self.profile.name,
            structured=True,
        )

    # ---- public ----------------------------------------------------------------------------
    def answer(self, test: TestCase, ctx: SpokeContext) -> AgentAnswer:
        try:
            return self._answer(test.question, ctx)
        except (MockError, SqlGenError) as exc:
            return AgentAnswer(meta=self.meta, error=str(exc))

    # ---- parsing ---------------------------------------------------------------------------
    def _rules(self, ctx: SpokeContext, kind: str) -> list[InstructionRule]:
        return [r for r in ctx.instructions.all_rules() if r.kind == kind]

    def _phrase_table(self, ctx: SpokeContext) -> dict[str, tuple[str, str, str | None]]:
        table: dict[str, tuple[str, str, str | None]] = {}
        for r in self._rules(ctx, "vocabulary"):
            view, name = str(r.data["view"]), str(r.data["name"])
            for p in r.data["phrases"]:
                table.setdefault(str(p), (view, name, r.rule_id))
        for v in ctx.model.views.values():
            for f in v.fields.values():
                if (
                    f.hidden
                    or f.has_tag("ai_hidden")
                    or f.has_tag("pii")
                    or f.kind == "dimension_group"
                ):
                    continue
                table.setdefault(f.display_label().lower(), (v.name, f.name, None))
        for phrase, target in self.profile.phrase_overrides:
            view, _, name = target.partition(".")
            if ctx.model.field(view, name) is not None:
                table[phrase] = (view, name, "vendor.system_instruction")
        return table

    def _parse(self, question: str, ctx: SpokeContext) -> _Parse | AgentAnswer:
        quotes = QUOTE_RE.findall(question)
        text = question.lower()
        for i, _ in enumerate(quotes):
            text = QUOTE_RE.sub(f" qv{i}qv ", text, count=1)
        text = re.sub(r"[?!,.;:]", " ", text)
        text = " " + re.sub(r"\s+", " ", text).strip() + " "
        out = _Parse()

        for r in self._rules(ctx, "pii_guardrail"):
            for p in r.data.get("phrases", []):
                if re.search(rf"\b{re.escape(str(p))}\b", text):
                    return AgentAnswer(
                        refused=True,
                        refusal_reason=f"request asks for personal data ({r.data['field']})",
                        applied_rules=[r.rule_id],
                        meta=self.meta,
                    )

        fiscal_rule = next(
            (r for r in self._rules(ctx, "time_convention") if "relative_periods" in r.data), None
        )
        periods: dict[str, str] = dict(fiscal_rule.data["relative_periods"]) if fiscal_rule else {}
        for phrase in sorted(periods, key=len, reverse=True):
            if f" {phrase} " in text:
                out.period = self._period(periods[phrase], ctx.as_of)
                text = text.replace(f" {phrase} ", " ", 1)
                if fiscal_rule:
                    out.rules.append(fiscal_rule.rule_id)
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

        table = self._phrase_table(ctx)
        taken: list[tuple[int, int]] = []
        matches: list[_Match] = []
        for phrase in sorted(table, key=lambda p: (-len(p), p)):
            for pm in re.finditer(rf"(?<![\w-]){re.escape(phrase)}(?![\w-])", text):
                s, e = pm.start(), pm.end()
                if any(s < te and e > ts for ts, te in taken):
                    continue
                view, name, rid = table[phrase]
                f = ctx.model.field(view, name)
                if f is None:
                    continue
                taken.append((s, e))
                matches.append(_Match(s, e, view, name, f.kind, rid))
        matches.sort(key=lambda m: m.start)

        filter_targets: dict[int, _Match] = {}
        for i, _ in enumerate(quotes):
            pos = text.find(f"qv{i}qv")
            prev = [m for m in matches if m.kind != "measure" and m.end <= pos]
            if prev:
                cand = prev[-1]
                between = text[cand.end : pos].split()
                if all(w in ("is", "of", "was", "were", "=", "had", "has", "for") for w in between):
                    filter_targets[i] = cand
        target_ids = {id(m) for m in filter_targets.values()}
        seen: set[tuple[str, str]] = set()
        for m in matches:
            if (m.view, m.name) in seen:
                continue
            seen.add((m.view, m.name))
            if m.rule_id:
                out.rules.append(m.rule_id)
            if m.kind == "measure":
                out.measures.append(m)
            elif id(m) not in target_ids:
                out.dims.append(m)
        out.quoted = [(q, filter_targets.get(i)) for i, q in enumerate(quotes)]
        return out

    @staticmethod
    def _period(kind: str, as_of: dt.date) -> tuple[dt.date, dt.date, str]:
        if kind == "last_completed_fiscal_quarter":
            return fiscal.last_completed_fiscal_quarter(as_of)
        if kind == "last_completed_fiscal_year":
            return fiscal.last_completed_fiscal_year(as_of)
        if kind == "last_completed_month":
            return fiscal.last_completed_month(as_of)
        raise MockError(f"unknown period {kind}")

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

    def _choose(
        self, parse: _Parse, ctx: SpokeContext
    ) -> tuple[ExploreRef, LExplore, dict[str, int]]:
        needed = [(m.view, m.name) for m in [*parse.measures, *parse.dims]]
        needed += [(m.view, m.name) for _, m in parse.quoted if m is not None]
        best: tuple[tuple[int, int, str], ExploreRef, LExplore, dict[str, int]] | None = None
        for ref in ctx.instructions.explores:
            e = ctx.model.explores[ref.explore]
            views = ctx.model.explore_views(e)
            dist = self._distances(e)
            cost = 0
            ok = True
            for view, _ in needed:
                ds = [dist.get(a, 99) for a, v in views.items() if v.name == view]
                if not ds:
                    ok = False
                    break
                cost += min(ds)
            if not ok:
                continue
            first = parse.measures[0].view if parse.measures else (needed[0][0] if needed else "")
            key = (0 if e.base_view == first else 1, cost, ref.explore)
            if best is None or key < best[0]:
                best = (key, ref, e, dist)
        if best is None:
            raise MockError(f"no explore contains all of {sorted({f'{v}.{n}' for v, n in needed})}")
        return best[1], best[2], best[3]

    def _alias(
        self, view: str, ref: ExploreRef, e: LExplore, dist: dict[str, int], ctx: SpokeContext
    ) -> str:
        views = ctx.model.explore_views(e)
        aliases = [a for a, v in views.items() if v.name == view]
        if ref.fiscal_alias and ref.fiscal_alias in aliases:
            return ref.fiscal_alias
        if e.base_alias in aliases:
            return e.base_alias
        return sorted(aliases, key=lambda a: (dist.get(a, 99), a))[0]

    # ---- value resolution ------------------------------------------------------------------
    def _distinct(self, ctx: SpokeContext, e: LExplore, fld: str) -> list[str]:
        key = (ctx.spoke, e.name, fld)
        if key not in self._distinct_cache:
            if ctx.con is None:
                raise MockError("mock runner needs a DuckDB connection")
            sql, _ = SqlBuilder(ctx.model, e).build([fld], [])
            self._distinct_cache[key] = [
                str(r[0]) for r in ctx.con.execute(sql).fetchall() if r[0] is not None
            ]
        return self._distinct_cache[key]

    def _resolve_values(self, raw: str, candidates: list[str]) -> list[str]:
        if not self.profile.fuzzy_values:
            return [v for v in candidates if v.strip().lower() == raw.strip().lower()]
        q = _norm(raw)
        out = []
        for v in candidates:
            n = _norm(v)
            if (
                n.startswith(q)
                or q.startswith(n)
                or difflib.SequenceMatcher(None, q, n).ratio() >= 0.85
            ):
                out.append(v)
        return sorted(out)

    def _value_filter(
        self,
        raw: str,
        target: _Match | None,
        ref: ExploreRef,
        e: LExplore,
        dist: dict[str, int],
        ctx: SpokeContext,
    ) -> ResolvedFilter:
        views = ctx.model.explore_views(e)
        if target is not None:
            cands = [f"{self._alias(target.view, ref, e, dist, ctx)}.{target.name}"]
        else:
            cands = []
            for alias in sorted(views, key=lambda a: (dist.get(a, 99), a)):
                for f in views[alias].fields.values():
                    if (
                        f.kind == "dimension"
                        and f.type == "string"
                        and not f.hidden
                        and not f.has_tag("pii")
                        and not f.has_tag("ai_hidden")
                    ):
                        cands.append(f"{alias}.{f.name}")
        for fld in cands:
            vals = self._resolve_values(raw, self._distinct(ctx, e, fld))
            if vals:
                return ResolvedFilter(field=fld, raw=raw, values=vals)
        raise MockError(f"could not resolve filter value {raw!r}")

    # ---- answer ----------------------------------------------------------------------------
    def _answer(self, question: str, ctx: SpokeContext) -> AgentAnswer:
        parsed = self._parse(question, ctx)
        if isinstance(parsed, AgentAnswer):
            return parsed
        if not parsed.measures:
            raise MockError("no measure recognized in question")
        ref, e, dist = self._choose(parsed, ctx)
        rules = list(parsed.rules)
        dims = [f"{self._alias(m.view, ref, e, dist, ctx)}.{m.name}" for m in parsed.dims]
        if parsed.timeframe:
            if not ref.default_time_field:
                raise MockError(f"explore {ref.explore} has no default time field")
            dims.append(f"{ref.default_time_field}_{parsed.timeframe}")
        measures = [f"{self._alias(m.view, ref, e, dist, ctx)}.{m.name}" for m in parsed.measures]

        chain = {ref.explore, *ref.extends_chain}
        filters: list[ResolvedFilter] = []
        for r in self._rules(ctx, "default_filter"):
            if r.data.get("explore") in chain:
                filters.append(
                    ResolvedFilter(
                        field=str(r.data["field"]),
                        raw=str(r.data["value"]),
                        values=[str(r.data["value"])],
                    )
                )
                rules.append(r.rule_id)
        for r in self._rules(ctx, "exclusion"):
            if r.data.get("explore") in chain:
                rules.append(r.rule_id)
        if parsed.period is not None:
            if not ref.default_time_field:
                raise MockError(f"explore {ref.explore} has no default time field")
            start, end, label = parsed.period
            filters.append(
                ResolvedFilter(
                    field=f"{ref.default_time_field}_date", raw=label, start=start, end=end
                )
            )
            trule = next(
                (
                    r
                    for r in self._rules(ctx, "time_convention")
                    if r.data.get("explore") in chain and "time_field" in r.data
                ),
                None,
            )
            if trule:
                rules.append(trule.rule_id)
        for raw, target in parsed.quoted:
            filters.append(self._value_filter(raw, target, ref, e, dist, ctx))

        qfilters = [
            QueryFilter(field=f.field, values=f.values, start=f.start, end=f.end) for f in filters
        ]
        sql, cols = SqlBuilder(ctx.model, e).build([*dims, *measures], qfilters)
        if ctx.con is None:
            raise MockError("mock runner needs a DuckDB connection")
        rows = [[_json_value(v) for v in row] for row in ctx.con.execute(sql).fetchall()]
        return AgentAnswer(
            explore=ref.explore,
            fields=cols,
            filters=filters,
            sql=sql,
            rows=rows,
            applied_rules=sorted(set(rules)),
            meta=self.meta,
        )
