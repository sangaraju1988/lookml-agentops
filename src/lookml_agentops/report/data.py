"""Assemble report data from run history. Reports contain summaries and structures only."""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, Field

from lookml_agentops.attribute.attribute import Attribution, attribute, headline
from lookml_agentops.verify.comparator import TestResult
from lookml_agentops.verify.history import History, RunInfo, RunRecord


class SpokeRow(BaseModel):
    spoke: str
    total: int
    passed: int
    degraded: int
    failed: int
    errors: int
    skipped: int
    golden_rate: float
    adherence_rate: float
    rate: float


class RuleRow(BaseModel):
    spoke: str
    rule_id: str
    kind: str
    tests: int
    passing: int

    @property
    def honored(self) -> bool:
        return self.passing == self.tests


class TrendPoint(BaseModel):
    run_id: str
    label: str
    runner: str
    rates: dict[str, float]


class ReportData(BaseModel):
    head: RunInfo
    spokes: list[SpokeRow]
    degraded: list[TestResult]
    failing: list[TestResult]
    rules: list[RuleRow]
    attribution: Attribution | None = None
    headline: list[str] = Field(default_factory=list)
    trend: list[TrendPoint] = Field(default_factory=list)
    result_only: bool = False


def _rate(xs: list[TestResult]) -> float:
    checkable = [r for r in xs if r.status != "skipped"]
    return (
        round(100.0 * sum(r.status == "pass" for r in checkable) / len(checkable), 1)
        if checkable
        else 0.0
    )


def spoke_rows(rec: RunRecord) -> list[SpokeRow]:
    out = []
    for spoke in sorted({r.spoke for r in rec.results}):
        rs = [r for r in rec.results if r.spoke == spoke]
        c = Counter(r.status for r in rs)
        out.append(
            SpokeRow(
                spoke=spoke,
                total=len(rs),
                passed=c["pass"],
                degraded=c["degraded"],
                failed=c["fail"],
                errors=c["error"],
                skipped=c["skipped"],
                golden_rate=_rate([r for r in rs if r.kind == "golden"]),
                adherence_rate=_rate([r for r in rs if r.kind == "adherence"]),
                rate=_rate(rs),
            )
        )
    return out


def rule_rows(rec: RunRecord) -> list[RuleRow]:
    acc: dict[tuple[str, str], list[TestResult]] = {}
    kinds: dict[str, str] = {}
    for r in rec.results:
        for rid in r.rule_ids:
            acc.setdefault((r.spoke, rid), []).append(r)
            kinds[rid] = next((t.split(":", 1)[1] for t in r.tags if t.startswith("rule:")), "")
    rows = [
        RuleRow(
            spoke=s,
            rule_id=rid,
            kind=kinds.get(rid, ""),
            tests=len(rs),
            passing=sum(x.status == "pass" for x in rs),
        )
        for (s, rid), rs in acc.items()
    ]
    return sorted(rows, key=lambda x: (x.honored, x.spoke, x.rule_id))


def build_report_data(
    history: History, head: str | None = None, base: str | None = None, trend: int = 10
) -> ReportData:
    ids = history.run_ids()
    if not ids:
        raise ValueError("no runs recorded yet; run `lkagent verify` first")
    head_id = head or ids[-1]
    rec = history.load(head_id)
    idx = ids.index(head_id)
    base_id = base if base is not None else (ids[idx - 1] if idx > 0 else None)
    att = attribute(history.load(base_id), rec) if base_id else None
    points = []
    for rid in ids[max(0, idx - trend + 1) : idx + 1]:
        r = history.load(rid)
        points.append(
            TrendPoint(
                run_id=rid,
                label=r.info.label or "",
                runner=r.info.runner_version,
                rates={s.spoke: s.rate for s in spoke_rows(r)},
            )
        )
    return ReportData(
        head=rec.info,
        spokes=spoke_rows(rec),
        degraded=[r for r in rec.results if r.status == "degraded"],
        failing=[r for r in rec.results if r.status in ("fail", "error")],
        rules=rule_rows(rec),
        attribution=att,
        headline=headline(att) if att else [],
        trend=points,
        result_only=any(r.mode == "result_only" for r in rec.results),
    )
