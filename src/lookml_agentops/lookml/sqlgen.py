"""Compile a Looker-style query (explore + fields + filters) to DuckDB SQL.

Deliberately small: it supports the LookML subset used by the examples and fails loudly on
anything else. Supported:

* ``${TABLE}``, ``${field}``, ``${alias.field}`` substitution (recursive), dimension-group
  timeframes (raw, time, date, week, month, quarter, year);
* measures of type count, count_distinct, sum, average, min, max, number, with ``filters:``;
* ``sql_table_name`` and ``derived_table.sql`` views, ``from:`` joins, left/inner joins;
* ``sql_always_where`` and categorical / date-range filters.

Symmetric aggregates are **not** implemented: measures reached through a one_to_many or
many_to_many join raise :class:`SqlGenError` instead of silently fanning out.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from dataclasses import field as dc_field

from lookml_agentops.lookml.model import LExplore, LField, LView
from lookml_agentops.lookml.resolve import EffectiveModel

REF_RE = re.compile(r"\$\{([^}]+)\}")
TIMEFRAMES = {"raw", "time", "date", "week", "month", "quarter", "year"}


class SqlGenError(Exception):
    pass


@dataclass
class QueryFilter:
    field: str  # alias.field (timeframe names allowed)
    values: list[str] = dc_field(default_factory=list)
    start: dt.date | None = None
    end: dt.date | None = None


@dataclass
class ResolvedRef:
    alias: str
    view: LView
    field: LField
    timeframe: str | None


def _lit(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


class SqlBuilder:
    def __init__(self, em: EffectiveModel, explore: LExplore) -> None:
        self.em = em
        self.explore = explore
        self.alias_views = em.explore_views(explore)
        missing = set(explore.aliases()) - set(self.alias_views)
        if missing:
            raise SqlGenError(
                f"explore {explore.name}: unknown views for aliases {sorted(missing)}"
            )
        self.used: set[str] = {explore.base_alias}

    # ---- field lookup ----------------------------------------------------------------------
    def resolve(self, ref: str, current_alias: str | None = None) -> ResolvedRef:
        if "." in ref:
            alias, name = ref.split(".", 1)
            if alias not in self.alias_views:
                # a view referring to itself by view name while joined under another alias
                if current_alias is not None and self.alias_views[current_alias].name == alias:
                    alias = current_alias
                else:
                    raise SqlGenError(
                        f"unknown alias {alias!r} in {ref!r} (explore {self.explore.name})"
                    )
        else:
            if current_alias is None:
                raise SqlGenError(f"unqualified reference {ref!r}")
            alias, name = current_alias, ref
        view = self.alias_views[alias]
        if name in view.fields:
            return ResolvedRef(alias, view, view.fields[name], None)
        for f in view.fields.values():
            if f.kind == "dimension_group" and name.startswith(f.name + "_"):
                tf = name[len(f.name) + 1 :]
                if tf in TIMEFRAMES:
                    return ResolvedRef(alias, view, f, tf)
        raise SqlGenError(f"unknown field {alias}.{name} (view {view.name})")

    # ---- expression expansion --------------------------------------------------------------
    def _subst(self, sql: str, alias: str, *, allow_measures: bool, stack: tuple[str, ...]) -> str:
        def repl(m: re.Match[str]) -> str:
            inner = m.group(1).strip()
            if inner == "TABLE":
                self.used.add(alias)
                return alias
            r = self.resolve(inner, alias)
            key = f"{r.alias}.{r.field.name}.{r.timeframe}"
            if key in stack:
                raise SqlGenError(f"circular reference: {' -> '.join((*stack, key))}")
            if r.field.kind == "measure":
                if not allow_measures:
                    raise SqlGenError(f"measure {inner} referenced from a dimension context")
                return "(" + self.measure_expr(r, stack=(*stack, key)) + ")"
            return "(" + self.dimension_expr(r, stack=(*stack, key)) + ")"

        return REF_RE.sub(repl, sql)

    def dimension_expr(self, r: ResolvedRef, *, stack: tuple[str, ...] = ()) -> str:
        self.used.add(r.alias)
        base_sql = r.field.sql or f"${{TABLE}}.{r.field.name}"
        base = self._subst(base_sql, r.alias, allow_measures=False, stack=stack)
        if r.field.kind != "dimension_group":
            return base
        tf = r.timeframe or "raw"
        if r.field.params.get("datatype") == "date" and tf in ("raw", "time"):
            return f"CAST({base} AS DATE)"
        return {
            "raw": base,
            "time": base,
            "date": f"CAST({base} AS DATE)",
            "week": f"CAST(date_trunc('week', {base}) AS DATE)",
            "month": f"strftime({base}, '%Y-%m')",
            "quarter": f"(CAST(year({base}) AS VARCHAR) || '-Q' || CAST(quarter({base}) AS VARCHAR))",
            "year": f"year({base})",
        }[tf]

    def _check_fanout(self, alias: str) -> None:
        if alias == self.explore.base_alias:
            return
        j = self.explore.joins.get(alias)
        if j is not None and j.relationship in ("one_to_many", "many_to_many"):
            raise SqlGenError(
                f"measure on {alias} is reached via a {j.relationship} join; symmetric aggregates "
                "are not supported by the mock SQL generator"
            )

    def _filter_cond(
        self, flt: list[dict[str, str]] | object, alias: str, stack: tuple[str, ...]
    ) -> str:
        conds: list[str] = []
        items = flt if isinstance(flt, list) else [flt]
        for item in items:
            if not isinstance(item, dict):
                continue
            for fname, value in item.items():
                r = self.resolve(fname, alias)
                expr = self.dimension_expr(r, stack=stack)
                conds.append(self._value_cond(r, expr, [str(value)]))
        return " AND ".join(conds) if conds else "TRUE"

    def _value_cond(self, r: ResolvedRef, expr: str, values: list[str]) -> str:
        if r.field.type == "yesno":
            parts = []
            for v in values:
                if v.strip().lower() == "yes":
                    parts.append(f"COALESCE({expr}, FALSE)")
                elif v.strip().lower() == "no":
                    parts.append(f"NOT COALESCE({expr}, FALSE)")
                else:
                    raise SqlGenError(f"yesno filter value must be yes/no, got {v!r}")
            return "(" + " OR ".join(parts) + ")"
        pos = [v for v in values if not v.startswith("-")]
        neg = [v[1:] for v in values if v.startswith("-")]
        conds = []
        if pos:
            conds.append(f"{expr} IN ({', '.join(_lit(v) for v in pos)})")
        if neg:
            conds.append(f"{expr} NOT IN ({', '.join(_lit(v) for v in neg)})")
        return "(" + " AND ".join(conds) + ")"

    def measure_expr(self, r: ResolvedRef, *, stack: tuple[str, ...] = ()) -> str:
        f = r.field
        self.used.add(r.alias)
        typ = f.type
        filters = f.params.get("filters")
        cond = self._filter_cond(filters, r.alias, stack) if filters else None

        def wrap(expr: str) -> str:
            return f"CASE WHEN {cond} THEN {expr} END" if cond else expr

        if typ == "number":
            if not f.sql:
                raise SqlGenError(f"measure {f.id} of type number needs sql")
            return self._subst(f.sql, r.alias, allow_measures=True, stack=stack)
        self._check_fanout(r.alias)
        if typ == "count":
            pk = next((x for x in r.view.fields.values() if x.primary_key), None)
            if pk is None:
                return f"COUNT({wrap('1')})" if cond else "COUNT(*)"
            pk_expr = self.dimension_expr(ResolvedRef(r.alias, r.view, pk, None), stack=stack)
            return f"COUNT(DISTINCT {wrap(pk_expr)})"
        if not f.sql:
            raise SqlGenError(f"measure {f.id} of type {typ} needs sql")
        inner = self._subst(f.sql, r.alias, allow_measures=False, stack=stack)
        agg = {
            "sum": "SUM",
            "average": "AVG",
            "min": "MIN",
            "max": "MAX",
            "count_distinct": "COUNT(DISTINCT",
        }
        if typ not in agg:
            raise SqlGenError(f"unsupported measure type {typ!r} ({f.id})")
        if typ == "count_distinct":
            return f"COUNT(DISTINCT {wrap(inner)})"
        return f"{agg[typ]}({wrap(inner)})"

    # ---- query -----------------------------------------------------------------------------
    def _from_clause(self, alias: str) -> str:
        v = self.alias_views[alias]
        if v.derived_sql:
            return f"({v.derived_sql.strip()}) AS {alias}"
        if v.sql_table_name:
            return f"{v.sql_table_name} AS {alias}"
        raise SqlGenError(f"view {v.name} has neither sql_table_name nor derived_table.sql")

    def build(
        self, fields: list[str], filters: list[QueryFilter], *, apply_always_where: bool = True
    ) -> tuple[str, list[str]]:
        dims: list[tuple[str, str]] = []
        measures: list[tuple[str, str]] = []
        for ref in fields:
            r = self.resolve(ref)
            if r.field.kind == "measure":
                measures.append((ref, self.measure_expr(r)))
            else:
                expr = self.dimension_expr(r)
                if r.field.type == "yesno":
                    expr = f"CASE WHEN {expr} THEN 'Yes' ELSE 'No' END"
                dims.append((ref, expr))
        where: list[str] = []
        if apply_always_where and self.explore.sql_always_where:
            where.append(
                "("
                + self._subst(
                    self.explore.sql_always_where,
                    self.explore.base_alias,
                    allow_measures=False,
                    stack=(),
                )
                + ")"
            )
        for flt in filters:
            r = self.resolve(flt.field)
            if flt.start is not None or flt.end is not None:
                raw = self.dimension_expr(ResolvedRef(r.alias, r.view, r.field, "raw"))
                if flt.start is not None:
                    where.append(f"{raw} >= TIMESTAMP '{flt.start.isoformat()} 00:00:00'")
                if flt.end is not None:
                    where.append(f"{raw} < TIMESTAMP '{flt.end.isoformat()} 00:00:00'")
            else:
                where.append(self._value_cond(r, self.dimension_expr(r), flt.values))
        joins = self._joins()
        cols = [f"{expr} AS c{i}" for i, (_, expr) in enumerate([*dims, *measures])]
        if not cols:
            raise SqlGenError("query selects no fields")
        sql = "SELECT " + ",\n       ".join(cols)
        sql += f"\nFROM {self._from_clause(self.explore.base_alias)}"
        for j in joins:
            sql += "\n" + j
        if where:
            sql += "\nWHERE " + "\n  AND ".join(where)
        if (dims and measures) or dims:
            sql += "\nGROUP BY " + ", ".join(str(i + 1) for i in range(len(dims)))
        if dims:
            sql += "\nORDER BY " + ", ".join(str(i + 1) for i in range(len(dims)))
        return sql, [ref for ref, _ in [*dims, *measures]]

    def _joins(self) -> list[str]:
        # expand join conditions until the set of used aliases is closed
        rendered: dict[str, str] = {}
        changed = True
        while changed:
            changed = False
            for j in self.explore.joins.values():
                if j.name in self.used and j.name not in rendered:
                    if not j.sql_on:
                        raise SqlGenError(f"join {j.name} has no sql_on")
                    on = self._subst(j.sql_on, j.name, allow_measures=False, stack=())
                    kind = {
                        "inner": "JOIN",
                        "left_outer": "LEFT JOIN",
                        "full_outer": "FULL OUTER JOIN",
                    }.get(j.join_type, "LEFT JOIN")
                    rendered[j.name] = f"{kind} {self._from_clause(j.name)} ON {on}"
                    changed = True
        return [rendered[j] for j in self.explore.joins if j in rendered]


def build_query(
    em: EffectiveModel, explore: str, fields: list[str], filters: list[QueryFilter]
) -> tuple[str, list[str]]:
    return SqlBuilder(em, em.explores[explore]).build(fields, filters)


def distinct_values_sql(em: EffectiveModel, explore: str, field: str) -> str:
    b = SqlBuilder(em, em.explores[explore])
    sql, _ = b.build([field], [])
    return sql
