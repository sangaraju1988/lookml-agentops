"""Structural + result comparison of an :class:`AgentAnswer` against a test's expectation.

Status:
* ``pass``      — structure (explore, fields, filters, guards) and result match;
* ``degraded``  — the result matches but the structure does not (a future failure);
* ``fail``      — the result or a required structural check is wrong;
* ``error``     — the runner could not answer;
* ``drift``     — the answer matches today's ground truth, but the ground truth itself moved
                  since the baseline (data changed, e.g. late-arriving refunds) — not a regression;
* ``skipped``   — nothing checkable (e.g. adherence test on a result-only runner).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from lookml_agentops._util.hashing import sha256_obj
from lookml_agentops.diagnose.loader import LoadedTest, expected_window
from lookml_agentops.diagnose.models import AgentAnswer, Tolerance

Status = Literal["pass", "fail", "degraded", "drift", "error", "skipped"]


class TestResult(BaseModel):
    __test__ = False
    test_id: str
    agent: str
    kind: str
    question: str = ""
    status: Status
    mode: Literal["structural", "result_only"]
    checks: dict[str, bool | None] = Field(default_factory=dict)
    details: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    test_hash: str = ""
    # structure only — never row-level data
    answer: dict[str, Any] = Field(default_factory=dict)
    gt_hash: str | None = None  # fingerprint of today's ground-truth result
    baseline_gt_hash: str | None = None
    deps: list[dict[str, Any]] = Field(default_factory=list)  # dependency records (see deps.py)


def _num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _close(a: float, b: float, tol: Tolerance) -> bool:
    rel = tol.relative or 1e-9
    return abs(a - b) <= tol.absolute + rel * max(abs(a), abs(b))


def _key(row: list[Any]) -> tuple[str, ...]:
    return tuple("" if isinstance(v, float) else str(v) for v in row)


def compare_rows(got: list[list[Any]], want: list[list[Any]], tol: Tolerance) -> tuple[bool, str]:
    if len(got) != len(want):
        return False, f"row count {len(got)} != expected {len(want)}"
    for g, w in zip(sorted(got, key=_key), sorted(want, key=_key), strict=True):
        if len(g) != len(w):
            return False, f"column count {len(g)} != expected {len(w)}"
        for a, b in zip(g, w, strict=True):
            if _num(a) and _num(b):
                if not _close(float(a), float(b), tol):
                    dims = [str(v) for v in w if not _num(v) and v is not None]
                    where = f" for {', '.join(dims)}" if dims else ""
                    return False, f"value {a} != expected {b}{where}"
            elif (a is None) != (b is None) or (a is not None and str(a) != str(b)):
                return False, f"value {a!r} != expected {b!r}"
    return True, "result matches"


def rows_hash(rows: list[list[Any]]) -> str:
    """Order-independent fingerprint of aggregated rows (floats rounded to 6 significant digits)."""
    norm = sorted(
        [f"{float(v):.6g}" if _num(v) else ("" if v is None else str(v)) for v in r] for r in rows
    )
    return sha256_obj(norm)[:16]


def summarize_answer(ans: AgentAnswer) -> dict[str, Any]:
    return {
        "explore": ans.explore,
        "fields": ans.fields,
        "filters": [
            {k: v for k, v in f.model_dump(mode="json").items() if v not in (None, [])}
            for f in ans.filters
        ],
        "refused": ans.refused,
        "applied_rules": ans.applied_rules,
        "row_count": None if ans.rows is None else len(ans.rows),
        "result_hash": None if ans.rows is None else sha256_obj(ans.rows)[:16],
        "error": ans.error,
    }


def compare(
    lt: LoadedTest,
    ans: AgentAnswer,
    *,
    as_of: dt.date,
    truth: list[list[Any]] | None,
    field_tags: Callable[[str | None, str], list[str]],
    baseline_gt_hash: str | None = None,
) -> TestResult:
    t = lt.test
    exp = t.expect
    structured = ans.meta.structured
    res = TestResult(
        test_id=t.id,
        agent=t.agent,
        question=t.question,
        kind=t.kind,
        status="pass",
        mode="structural" if structured else "result_only",
        tags=t.tags,
        rule_ids=t.rule_ids,
        test_hash=lt.test_hash,
        answer=summarize_answer(ans),
    )
    if ans.error:
        res.status = "error"
        res.details.append(f"runner error: {ans.error}")
        return res

    if exp.refuse_or_exclude_tags:
        leaked = [
            f
            for f in ans.fields
            if set(field_tags(ans.explore, f)) & set(exp.refuse_or_exclude_tags)
        ]
        ok = ans.refused or (not leaked and structured)
        res.checks["refusal"] = ok
        res.details.append(
            "refused" if ans.refused else (f"leaked {leaked}" if leaked else "answered without PII")
        )
        res.status = "pass" if ok else "fail"
        return res
    if ans.refused:
        res.checks["refusal"] = False
        res.details.append(f"unexpected refusal: {ans.refusal_reason}")
        res.status = "fail"
        return res

    if structured:
        if exp.explore is not None:
            res.checks["explore"] = ans.explore == exp.explore
            if not res.checks["explore"]:
                res.details.append(f"explore {ans.explore} != expected {exp.explore}")
        if exp.fields_any_of is not None:
            ok = any(set(opt) <= set(ans.fields) for opt in exp.fields_any_of)
            res.checks["fields"] = ok
            if not ok:
                res.details.append(f"fields {ans.fields} do not include any of {exp.fields_any_of}")
        if exp.filters:
            ok = True
            for fe in exp.filters:
                got = [f for f in ans.filters if f.field == fe.field]
                if not got:
                    ok = False
                    res.details.append(f"missing filter on {fe.field}")
                    continue
                g = got[0]
                if fe.values is not None:
                    if sorted(g.values) != sorted(fe.values):
                        ok = False
                        res.details.append(
                            f"filter {fe.field} resolved to {g.values}, expected {fe.values}"
                        )
                elif g.start is None and g.end is None and not g.values:
                    # runner exposes only the raw expression (e.g. "last quarter"), not the window
                    res.details.append(
                        f"filter {fe.field} window not exposed by runner ({g.raw!r}); not checked"
                    )
                else:
                    win = expected_window(fe, as_of)
                    if win is None or (g.start, g.end) != win:
                        ok = False
                        res.details.append(
                            f"filter {fe.field} window {g.start}..{g.end}, expected {win[0] if win else '?'}..{win[1] if win else '?'}"
                        )
            res.checks["filters"] = ok
        if exp.context_rules:
            if ans.context_rules is None:
                res.checks["context"] = None
                res.details.append(
                    "runner cannot report its instruction context; presence not checked"
                )
            else:
                missing_rules = [r for r in exp.context_rules if r not in ans.context_rules]
                res.checks["context"] = not missing_rules
                if missing_rules:
                    res.details.append(f"rules missing from the agent's context: {missing_rules}")
        if exp.sql_contains:
            sql = (ans.sql or "").lower()
            missing = [m for m in exp.sql_contains if m.lower() not in sql]
            res.checks["sql_guard"] = not missing
            if missing:
                res.details.append(f"generated SQL lacks {missing}")

    if truth is not None:
        if ans.rows is None:
            res.checks["result"] = None
            res.details.append("runner returned no result rows")
        else:
            ok, msg = compare_rows(ans.rows, truth, exp.tolerance)
            res.checks["result"] = ok
            if not ok:
                res.details.append(msg)

    structural = [v for k, v in res.checks.items() if k != "result" and v is not None]
    result_ok = res.checks.get("result")
    if truth is not None:
        res.gt_hash = rows_hash(truth)
        res.baseline_gt_hash = baseline_gt_hash
    drifted = truth is not None and baseline_gt_hash is not None and res.gt_hash != baseline_gt_hash
    if not structural and result_ok is None:
        res.status = "skipped"
        res.details.append("nothing checkable with this runner")
    elif all(structural) and result_ok is True and drifted:
        res.status = "drift"
        res.details.append(
            "ground truth moved since the baseline; the answer matches the new truth"
        )
    elif all(structural) and result_ok in (True, None):
        res.status = "pass"
    elif result_ok is True and not all(structural):
        res.status = "degraded"
    else:
        res.status = "fail"
    return res
