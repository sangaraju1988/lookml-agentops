"""Assemble report data from run history. Reports carry summaries and structure only."""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, Field

from lookml_agentops.diagnose.comparator import TestResult
from lookml_agentops.diagnose.history import History, RunInfo, RunRecord
from lookml_agentops.diagnose.why import Diagnosis, Verdict, diagnose, headline
from lookml_agentops.inputs.owners import Owners


class AgentRow(BaseModel):
    agent: str
    total: int
    passed: int
    degraded: int
    drift: int
    failed: int
    errors: int
    skipped: int
    suite_rate: float
    generated_rate: float
    rate: float


class ElementRow(BaseModel):
    agent: str
    element: str
    kind: str
    tests: int
    passing: int

    @property
    def honored(self) -> bool:
        return self.passing == self.tests


class OwnerGroup(BaseModel):
    owner: str
    by_cause: dict[str, list[Verdict]]

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.by_cause.values())


class TrendPoint(BaseModel):
    run_id: str
    label: str
    runner: str
    rates: dict[str, float]


class ReportData(BaseModel):
    head: RunInfo
    agents: list[AgentRow]
    degraded: list[TestResult]
    failing: list[TestResult]
    drift: list[TestResult]
    elements: list[ElementRow]
    diagnosis: Diagnosis | None = None
    owners: list[OwnerGroup] = Field(default_factory=list)
    headline: list[str] = Field(default_factory=list)
    trend: list[TrendPoint] = Field(default_factory=list)
    result_only: bool = False


def _rate(xs: list[TestResult]) -> float:
    """Share of checkable tests answered correctly; drift counts as correct (the data moved)."""
    checkable = [r for r in xs if r.status != "skipped"]
    ok = sum(r.status in ("pass", "drift") for r in checkable)
    return round(100.0 * ok / len(checkable), 1) if checkable else 0.0


def agent_rows(rec: RunRecord) -> list[AgentRow]:
    out = []
    for agent in sorted({r.agent for r in rec.results}):
        rs = [r for r in rec.results if r.agent == agent]
        c = Counter(r.status for r in rs)
        out.append(
            AgentRow(
                agent=agent,
                total=len(rs),
                passed=c["pass"],
                degraded=c["degraded"],
                drift=c["drift"],
                failed=c["fail"],
                errors=c["error"],
                skipped=c["skipped"],
                suite_rate=_rate([r for r in rs if r.kind == "golden"]),
                generated_rate=_rate([r for r in rs if r.kind == "adherence"]),
                rate=_rate(rs),
            )
        )
    return out


def element_rows(rec: RunRecord) -> list[ElementRow]:
    acc: dict[tuple[str, str], list[TestResult]] = {}
    for r in rec.results:
        if r.kind != "adherence":
            continue
        for el in r.rule_ids:
            acc.setdefault((r.agent, el), []).append(r)
    rows = [
        ElementRow(
            agent=a,
            element=el,
            kind=el.split(":", 1)[0],
            tests=len(rs),
            passing=sum(x.status == "pass" for x in rs),
        )
        for (a, el), rs in acc.items()
    ]
    return sorted(rows, key=lambda x: (x.honored, x.agent, x.element))


def owner_groups(d: Diagnosis) -> list[OwnerGroup]:
    out = []
    for owner, vs in d.by_owner().items():
        by: dict[str, list[Verdict]] = {}
        for v in vs:
            by.setdefault(v.cause, []).append(v)
        out.append(OwnerGroup(owner=owner, by_cause=dict(sorted(by.items()))))
    return out


def build_report_data(
    history: History,
    owners: Owners,
    head: str | None = None,
    base: str | None = None,
    trend: int = 10,
) -> ReportData:
    ids = history.run_ids()
    if not ids:
        raise ValueError("no runs recorded yet; run `lkagent diagnose run` first")
    head_id = head or ids[-1]
    rec = history.load(head_id)
    idx = ids.index(head_id)
    base_id = base if base is not None else (ids[idx - 1] if idx > 0 else None)
    d = diagnose(history.load(base_id), rec, owners) if base_id else None
    points = []
    for rid in ids[max(0, idx - trend + 1) : idx + 1]:
        r = history.load(rid)
        points.append(
            TrendPoint(
                run_id=rid,
                label=r.info.label or "",
                runner=r.info.runner_version,
                rates={a.agent: a.rate for a in agent_rows(r)},
            )
        )
    return ReportData(
        head=rec.info,
        agents=agent_rows(rec),
        degraded=[r for r in rec.results if r.status == "degraded"],
        failing=[r for r in rec.results if r.status in ("fail", "error")],
        drift=[r for r in rec.results if r.status == "drift"],
        elements=element_rows(rec),
        diagnosis=d,
        owners=owner_groups(d) if d else [],
        headline=headline(d) if d else [],
        trend=points,
        result_only=any(r.mode == "result_only" for r in rec.results),
    )
