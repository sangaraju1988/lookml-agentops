"""Normalize vendor responses (CA chat messages, MCP tool results) into :class:`AgentAnswer`.

TODO(verify-api): the CA reference is ambiguous about where a data message carries the Looker
query; we accept ``data.generatedLookerQuery`` and ``data.query.looker``. Its shape
(``model, explore, fields[], filters[{field, value}]``) matches the documented LookerQuery used
by ``lookerGoldenQueries``. When no Looker query is present the answer is marked
``structured=False`` and the comparator falls back to result-only comparison.
"""

from __future__ import annotations

import contextlib
import re
from typing import Any

from lookml_agentops.verify.models import AgentAnswer, ResolvedFilter, RunnerMeta

NUMERIC_TYPES = {
    "INTEGER",
    "INT64",
    "FLOAT",
    "FLOAT64",
    "NUMERIC",
    "BIGNUMERIC",
    "NUMBER",
    "DOUBLE",
}
DATEISH = re.compile(
    r"(\d{4}-\d{2}|\b(last|this|next|ago|before|after|quarter|year|month|week|day)s?\b)", re.I
)


def _looker_query(data: dict[str, Any]) -> dict[str, Any] | None:
    lq = data.get("generatedLookerQuery")
    if isinstance(lq, dict):
        return lq
    q = data.get("query")
    looker = q.get("looker") if isinstance(q, dict) else None
    return looker if isinstance(looker, dict) else None


def _filters(lq: dict[str, Any]) -> list[ResolvedFilter]:
    out = []
    for f in lq.get("filters") or []:
        field, value = str(f.get("field", "")), str(f.get("value", ""))
        if DATEISH.search(value):
            # a Looker date expression; the resolved window is not exposed
            out.append(ResolvedFilter(field=field, raw=value))
        else:
            out.append(
                ResolvedFilter(
                    field=field,
                    raw=value,
                    values=[v.strip() for v in value.split(",") if v.strip()],
                )
            )
    return out


def _rows(result: dict[str, Any]) -> list[list[Any]] | None:
    data = result.get("data")
    if not isinstance(data, list):
        return None
    fields = [f for f in (result.get("schema") or {}).get("fields", []) if isinstance(f, dict)]
    names = [str(f.get("name")) for f in fields] or (
        sorted(data[0]) if data and isinstance(data[0], dict) else []
    )
    numeric = {
        str(f.get("name")) for f in fields if str(f.get("type", "")).upper() in NUMERIC_TYPES
    }
    # our convention: dimensions first, then measures
    ordered = [n for n in names if n not in numeric] + [n for n in names if n in numeric]
    rows = []
    for rec in data:
        if not isinstance(rec, dict):
            continue
        row = []
        for n in ordered:
            v = rec.get(n)
            if n in numeric and isinstance(v, str):
                with contextlib.suppress(ValueError):
                    v = float(v)
            row.append(v)
        rows.append(row)
    return rows


def parse_ca_messages(messages: list[dict[str, Any]], meta: RunnerMeta) -> AgentAnswer:
    lq: dict[str, Any] | None = None
    sql: str | None = None
    rows: list[list[Any]] | None = None
    errors: list[str] = []
    final_text: list[str] = []
    saw_data = False
    for m in messages:
        sm = m.get("systemMessage") if isinstance(m, dict) else None
        if not isinstance(sm, dict):
            continue
        if isinstance(sm.get("data"), dict):
            saw_data = True
            d = sm["data"]
            lq = _looker_query(d) or lq
            sql = d.get("generatedSql") or sql
            if isinstance(d.get("result"), dict):
                rows = _rows(d["result"])
        if isinstance(sm.get("error"), dict):
            errors.append(str(sm["error"].get("text", "error")))
        text = sm.get("text")
        if isinstance(text, dict) and text.get("textType") == "FINAL_RESPONSE":
            final_text += [str(p) for p in text.get("parts", [])]
    structured = lq is not None
    meta = meta.model_copy(update={"structured": structured})
    if errors and not saw_data:
        return AgentAnswer(meta=meta, error="; ".join(errors))
    if not saw_data:
        # answered in prose without querying data (e.g. declined a PII request)
        return AgentAnswer(
            meta=meta.model_copy(update={"structured": True}),
            refused=True,
            refusal_reason=" ".join(final_text)[:500] or "no data returned",
        )
    return AgentAnswer(
        explore=str(lq["explore"]) if lq and "explore" in lq else None,
        fields=[str(f) for f in (lq or {}).get("fields", [])],
        filters=_filters(lq) if lq else [],
        sql=sql,
        rows=rows,
        meta=meta,
    )


def parse_tool_payload(payload: Any, meta: RunnerMeta) -> AgentAnswer:
    """MCP tool output: accept CA-style message lists, or a dict with a Looker query + rows."""
    if (
        isinstance(payload, list)
        and payload
        and isinstance(payload[0], dict)
        and ("systemMessage" in payload[0] or "userMessage" in payload[0])
    ):
        return parse_ca_messages(payload, meta)
    if isinstance(payload, dict):
        lq = _looker_query(payload) or (
            payload if "explore" in payload and "fields" in payload else None
        )
        result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
        return AgentAnswer(
            explore=str(lq["explore"]) if lq else None,
            fields=[str(f) for f in (lq or {}).get("fields", [])],
            filters=_filters(lq) if lq else [],
            sql=payload.get("generatedSql") or payload.get("sql"),
            rows=_rows(result) if isinstance(result, dict) else None,
            meta=meta.model_copy(update={"structured": lq is not None}),
        )
    return AgentAnswer(meta=meta.model_copy(update={"structured": False}), rows=None)
