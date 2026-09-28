"""``lkagent diagnose why``: for each test whose outcome changed, what changed and whose problem it is.

For each changed test, only the elements the test depends on (from run B) are compared:

1. exactly one tracked input (LookML project, agent spec, catalog, suite) changed among the
   test's dependencies -> verdict ``input`` naming that input, the element/parameter diff and its
   owner; confidence ``high`` when one element changed, ``medium`` when several in one input;
2. several inputs changed -> verdict ``multiple``, ordered by closeness to the failure; a changed
   element the test directly exercises is named as the likely cause; confidence ``low``;
3. no tracked model/spec/catalog/suite dependency changed but the test's ground-truth result did
   -> verdict ``data`` (the expected answer moved, e.g. late-arriving refunds; not a regression);
4. nothing tracked changed -> verdict ``external`` (vendor/runtime behaviour); grouped by trap tag.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, Field

from lookml_agentops.diagnose.comparator import TestResult
from lookml_agentops.diagnose.history import RunRecord
from lookml_agentops.diagnose.modeldiff import ElementChange, diff_element
from lookml_agentops.inputs.owners import UNASSIGNED, Owners

Cause = Literal["input", "multiple", "data", "external"]
Confidence = Literal["high", "medium", "low"]

TAG_HINTS = {
    "trap:dirty-categorical": "likely a filter-value resolution change (e.g. fuzzy matching of categorical values)",
    "trap:revenue-ambiguity": "likely a change in how vendor system instructions combine with your rules",
    "spec:rule": "likely a change in how vendor system instructions combine with your rules",
    "spec:vocabulary": "likely a change in how vendor instructions combine with your vocabulary",
    "trap:fiscal-calendar": "likely a change in relative-date / fiscal period interpretation",
    "trap:timezone": "likely a change in time zone handling",
    "trap:pii": "likely a change in guardrail handling",
    "spec:guardrail": "likely a change in guardrail handling",
    "trap:test-accounts": "likely a change in how explore guards are applied",
    "trap:late-refunds": "likely a change in metric selection or period attribution",
    "trap:void-invoices": "likely a change in how default filters are applied",
    "spec:golden-query": "likely a change in explore or field selection",
}


class ChangedDep(BaseModel):
    element_id: str
    role: str
    rank: int
    input_id: str
    owner: str
    change: ElementChange


class Verdict(BaseModel):
    agent: str
    test_id: str
    kind: str
    before: str
    after: str
    direction: Literal["regression", "fix", "other"]
    cause: Cause
    input_id: str | None = None
    owner: str
    confidence: Confidence
    likely_cause: str | None = None
    changes: list[ChangedDep] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    answer_before: dict[str, Any] = Field(default_factory=dict)
    answer_after: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class ExternalGroup(BaseModel):
    tag: str
    count: int
    all_changes: bool
    hint: str


class Diagnosis(BaseModel):
    run_a: str
    run_b: str
    started_a: str
    started_b: str
    runner_a: str
    runner_b: str
    verdicts: list[Verdict]
    external_groups: list[ExternalGroup]
    unchanged: int
    unchanged_inputs: list[str] = Field(default_factory=list)
    changed_inputs: list[str] = Field(default_factory=list)

    def by_owner(self) -> dict[str, list[Verdict]]:
        out: dict[str, list[Verdict]] = {}
        for v in self.verdicts:
            out.setdefault(v.owner, []).append(v)
        return dict(sorted(out.items()))

    def counts(self) -> dict[str, int]:
        c = Counter(v.cause for v in self.verdicts)
        return {k: c.get(k, 0) for k in ("input", "multiple", "data", "external")}


def _direction(before: str, after: str) -> Literal["regression", "fix", "other"]:
    if before in ("pass", "drift") and after not in ("pass", "drift"):
        return "regression"
    if before not in ("pass", "drift") and after in ("pass", "drift"):
        return "fix"
    return "other"


def _owner(owners: Owners, input_id: str, location: str | None) -> str:
    path = None
    if location:
        loc = location.rsplit(":", 1)[0]
        path = loc.split("/", 1)[1] if input_id.startswith("lookml:") and "/" in loc else loc
    return owners.owner(input_id, path)


def _answer_diff(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    out = []
    for k in ("explore", "fields", "filters", "refused", "row_count", "result_hash", "error"):
        if a.get(k) != b.get(k):
            out.append(f"answer {k}: {a.get(k)!r} -> {b.get(k)!r}")
    return out


def verdict_for(
    a: RunRecord, b: RunRecord, ra: TestResult, rb: TestResult, owners: Owners
) -> Verdict:
    changed: list[ChangedDep] = []
    data_changed = False
    runner_changed: list[str] = []
    for d in rb.deps:
        eid = d["element_id"]
        ea, eb = a.elements.get(eid), b.elements.get(eid)
        for ch in diff_element(ea, eb):
            if ch.facet not in d["facets"]:
                continue
            if eid.startswith("gt:"):
                data_changed = True
            elif eid.startswith("runner:"):
                runner_changed.append(ch.describe())
            else:
                changed.append(
                    ChangedDep(
                        element_id=eid,
                        role=d["role"],
                        rank=d["rank"],
                        input_id=ch.input_id,
                        owner=_owner(owners, ch.input_id, ch.location),
                        change=ch,
                    )
                )
    changed.sort(key=lambda c: (c.rank, c.element_id, c.change.facet))
    evidence = _answer_diff(ra.answer, rb.answer)
    base: dict[str, Any] = {
        "agent": rb.agent,
        "test_id": rb.test_id,
        "kind": rb.kind,
        "before": ra.status,
        "after": rb.status,
        "direction": _direction(ra.status, rb.status),
        "answer_before": ra.answer,
        "answer_after": rb.answer,
        "tags": rb.tags,
    }
    if changed:
        inputs = list(dict.fromkeys(c.input_id for c in changed))
        elements = list(dict.fromkeys(c.element_id for c in changed if c.rank < 3))
        exercised = next((c for c in changed if c.rank == 0), changed[0])
        likely = f"{exercised.change.describe()}"
        if len(inputs) == 1:
            conf: Confidence = "high" if len(elements) <= 1 else "medium"
            return Verdict(
                **base,
                cause="input",
                input_id=inputs[0],
                owner=changed[0].owner,
                confidence=conf,
                likely_cause=likely,
                changes=changed,
                evidence=evidence,
            )
        return Verdict(
            **base,
            cause="multiple",
            input_id=exercised.input_id,
            owner=exercised.owner,
            confidence="low",
            likely_cause=likely,
            changes=changed,
            evidence=[f"changed inputs: {', '.join(inputs)}", *evidence],
        )
    if data_changed:
        gt_owner = owners.owner("data:warehouse")
        return Verdict(
            **base,
            cause="data",
            input_id="data:warehouse",
            owner=gt_owner,
            confidence="high",
            likely_cause=f"ground truth for {rb.test_id} changed ({ra.gt_hash} -> {rb.gt_hash}); "
            "the expected answer moved, not the agent",
            evidence=["no tracked model/spec/catalog/suite dependency changed", *evidence],
        )
    rid = f"runner:{b.info.runner}"
    ev = runner_changed or ["no tracked input changed; the runner reported the same version"]
    return Verdict(
        **base,
        cause="external",
        input_id=rid,
        owner=owners.owner(rid),
        confidence="high" if runner_changed else "medium",
        likely_cause="vendor or runtime behaviour change",
        evidence=[*ev, *evidence],
    )


def diagnose(a: RunRecord, b: RunRecord, owners: Owners) -> Diagnosis:
    before = {(r.agent, r.test_id): r for r in a.results}
    verdicts: list[Verdict] = []
    unchanged = 0
    for rb in b.results:
        ra = before.get((rb.agent, rb.test_id))
        if ra is None:
            continue
        if ra.status == rb.status:
            unchanged += 1
            continue
        verdicts.append(verdict_for(a, b, ra, rb, owners))
    verdicts.sort(key=lambda v: (v.owner, v.agent, v.test_id))
    ext = [v for v in verdicts if v.cause == "external"]
    tags = Counter(t for v in ext for t in set(v.tags) if t.startswith(("trap:", "spec:")))
    groups = [
        ExternalGroup(
            tag=t,
            count=n,
            all_changes=n == len(ext),
            hint=TAG_HINTS.get(t, "vendor behaviour change"),
        )
        for t, n in sorted(tags.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    changed_inputs = sorted(
        i for i in b.inputs if a.inputs.get(i) is None or a.inputs[i].version != b.inputs[i].version
    )
    unchanged_inputs = sorted(
        i for i in b.inputs if i in a.inputs and a.inputs[i].version == b.inputs[i].version
    )
    return Diagnosis(
        run_a=a.info.run_id,
        run_b=b.info.run_id,
        started_a=a.info.started_at.isoformat(),
        started_b=b.info.started_at.isoformat(),
        runner_a=a.info.runner_version,
        runner_b=b.info.runner_version,
        verdicts=verdicts,
        external_groups=groups,
        unchanged=unchanged,
        unchanged_inputs=unchanged_inputs,
        changed_inputs=changed_inputs,
    )


def headline(d: Diagnosis) -> list[str]:
    total = len(d.verdicts)
    if total == 0:
        return [f"{d.run_a} -> {d.run_b}: no test outcome changed"]
    c = d.counts()
    out = [
        f"{d.run_a} -> {d.run_b}: {total} changed test(s) ("
        + ", ".join(f"{n} {k}" for k, n in c.items() if n)
        + ")"
    ]
    ext = [v for v in d.verdicts if v.cause == "external"]
    if ext:
        full = [g for g in d.external_groups if g.all_changes and g.tag.startswith("trap:")] or [
            g for g in d.external_groups if g.all_changes
        ]
        tail = f" between {d.started_a[:10]} and {d.started_b[:10]}"
        if full:
            out.append(
                f"external: {len(ext)} failure(s), all {full[0].tag} -> {full[0].hint}{tail}"
            )
        elif d.external_groups:
            g = d.external_groups[0]
            out.append(
                f"external: {len(ext)} failure(s), most {g.tag} ({g.count}) -> {g.hint}{tail}"
            )
        else:
            out.append(f"external: {len(ext)} failure(s){tail}")
    for owner, vs in d.by_owner().items():
        causes = sorted({v.likely_cause or v.cause for v in vs if v.cause in ("input", "multiple")})
        if causes:
            agents = sorted({v.agent for v in vs})
            out.append(f"{owner}: {len(vs)} test(s) in {', '.join(agents)} <- {'; '.join(causes)}")
    data = [v for v in d.verdicts if v.cause == "data"]
    if data:
        out.append(
            f"data: {len(data)} test(s) whose ground truth moved (drift, not a regression; "
            "accept with `diagnose run --rebaseline`)"
        )
    return out


def render_text(d: Diagnosis) -> str:
    lines = headline(d)
    for v in d.verdicts:
        lines.append(
            f"  {v.cause.upper():<8} {v.agent}/{v.test_id}: {v.before} -> {v.after}  "
            f"owner={v.owner or UNASSIGNED}  confidence={v.confidence}"
        )
        if v.likely_cause and v.cause != "external":
            lines.append(f"           likely: {v.likely_cause}")
        for c in v.changes[:3]:
            for p in c.change.params[:2]:
                lines.append(f"           {c.element_id} {p.param}: {p.old!r} -> {p.new!r}")
    return "\n".join(lines) + "\n"
