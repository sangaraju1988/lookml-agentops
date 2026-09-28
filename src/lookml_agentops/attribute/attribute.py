"""Change attribution between two recorded runs.

For each test whose status changed:

* hub and spoke revisions, instruction layer hashes, seed data and golden test all unchanged
  -> ``vendor`` (the only thing left is the agent/vendor behaviour);
* only the hub changed (tree hash or hub-layer hash) -> ``hub``;
* only this spoke changed -> ``spoke``;
* anything else -> ``unknown`` with the list of what changed.

Vendor-attributed changes are grouped by trap/rule tag to hint at *which* vendor behaviour moved.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from lookml_agentops.verify.comparator import TestResult
from lookml_agentops.verify.history import RunInfo, RunRecord

Cause = Literal["vendor", "hub", "spoke", "unknown"]

TAG_HINTS = {
    "trap:dirty-categorical": "likely a filter-value resolution change (e.g. fuzzy matching of categorical values)",
    "trap:revenue-ambiguity": "likely a change in how vendor system instructions combine with your vocabulary rules",
    "rule:vocabulary": "likely a change in how vendor system instructions combine with your vocabulary rules",
    "trap:fiscal-calendar": "likely a change in relative-date / fiscal period interpretation",
    "rule:time_convention": "likely a change in relative-date / fiscal period interpretation",
    "trap:timezone": "likely a change in time zone handling",
    "trap:pii": "likely a change in PII guardrail handling",
    "rule:pii_guardrail": "likely a change in PII guardrail handling",
    "trap:test-accounts": "likely a change in how default filters / explore guards are applied",
    "rule:exclusion": "likely a change in how default filters / explore guards are applied",
    "rule:default_filter": "likely a change in how default filters / explore guards are applied",
    "trap:late-refunds": "likely a change in metric selection or period attribution",
    "trap:void-invoices": "likely a change in how default filters are applied",
}


class Change(BaseModel):
    spoke: str
    test_id: str
    kind: str
    before: str
    after: str
    direction: Literal["regression", "fix", "other"]
    cause: Cause
    changed: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    details: list[str] = Field(default_factory=list)


class VendorGroup(BaseModel):
    tag: str
    count: int
    all_changes: bool
    hint: str


class Attribution(BaseModel):
    base_run: str
    head_run: str
    base: RunInfo
    head: RunInfo
    changes: list[Change]
    vendor_groups: list[VendorGroup]
    context: list[str]
    unchanged: int

    def counts(self) -> dict[str, int]:
        c = Counter(ch.cause for ch in self.changes)
        return {k: c.get(k, 0) for k in ("vendor", "hub", "spoke", "unknown")}


def _direction(before: str, after: str) -> Literal["regression", "fix", "other"]:
    if before == "pass" and after != "pass":
        return "regression"
    if before != "pass" and after == "pass":
        return "fix"
    return "other"


def _tree(info: RunInfo, project: str) -> str | None:
    rev = info.revisions.get(project) or {}
    v = rev.get("tree_hash")
    return None if v is None else str(v)


def _layer(info: RunInfo, spoke: str, layer: str) -> str | None:
    v = (info.instruction_hashes.get(spoke) or {}).get("layers", {}).get(layer)
    return None if v is None else str(v)


def _context(base: RunInfo, head: RunInfo) -> list[str]:
    out = []
    if (base.runner, base.runner_version, base.vendor_profile) != (
        head.runner,
        head.runner_version,
        head.vendor_profile,
    ):
        out.append(f"runner changed: {base.runner_version} -> {head.runner_version}")
    if base.as_of != head.as_of:
        out.append(f"as_of changed: {base.as_of} -> {head.as_of}")
    for p in sorted(set(base.revisions) | set(head.revisions)):
        if _tree(base, p) != _tree(head, p):
            out.append(
                f"project {p} changed: {str(_tree(base, p))[:10]} -> {str(_tree(head, p))[:10]}"
            )
    if base.data_hash != head.data_hash:
        out.append("seed data changed")
    return out


def classify(
    base: RunInfo, head: RunInfo, b: TestResult, h: TestResult
) -> tuple[Cause, list[str], list[str]]:
    hub = head.hub
    spoke = h.spoke
    changed: list[str] = []
    if _tree(base, hub) != _tree(head, hub) or _layer(base, spoke, f"hub:{hub}") != _layer(
        head, spoke, f"hub:{hub}"
    ):
        changed.append("hub")
    if _tree(base, spoke) != _tree(head, spoke) or _layer(base, spoke, f"spoke:{spoke}") != _layer(
        head, spoke, f"spoke:{spoke}"
    ):
        changed.append("spoke")
    if base.data_hash != head.data_hash:
        changed.append("seed data")
    if base.as_of != head.as_of:
        changed.append("as_of")
    if h.kind == "golden" and b.test_hash != h.test_hash:
        changed.append("golden test")
    evidence: list[str] = []
    if base.runner_version != head.runner_version:
        evidence.append(f"runner {base.runner_version} -> {head.runner_version}")
    if not changed:
        if not evidence:
            evidence.append(
                "no recorded input changed; vendor behaviour changed (or is nondeterministic)"
            )
        return "vendor", changed, evidence
    if changed == ["hub"]:
        return "hub", changed, evidence
    if changed == ["spoke"]:
        return "spoke", changed, evidence
    return "unknown", changed, evidence


def attribute(base: RunRecord, head: RunRecord) -> Attribution:
    before = {(r.spoke, r.test_id): r for r in base.results}
    changes: list[Change] = []
    unchanged = 0
    for h in head.results:
        b = before.get((h.spoke, h.test_id))
        if b is None:
            continue
        if b.status == h.status:
            unchanged += 1
            continue
        cause, changed, evidence = classify(base.info, head.info, b, h)
        changes.append(
            Change(
                spoke=h.spoke,
                test_id=h.test_id,
                kind=h.kind,
                before=b.status,
                after=h.status,
                direction=_direction(b.status, h.status),
                cause=cause,
                changed=changed,
                evidence=evidence,
                tags=h.tags,
                rule_ids=h.rule_ids,
                details=h.details,
            )
        )
    changes.sort(key=lambda c: (c.cause, c.spoke, c.test_id))
    vendor = [c for c in changes if c.cause == "vendor"]
    tag_counts = Counter(t for c in vendor for t in set(c.tags) if t.startswith(("trap:", "rule:")))
    groups = [
        VendorGroup(
            tag=tag,
            count=n,
            all_changes=n == len(vendor),
            hint=TAG_HINTS.get(tag, "vendor behaviour change"),
        )
        for tag, n in sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return Attribution(
        base_run=base.info.run_id,
        head_run=head.info.run_id,
        base=base.info,
        head=head.info,
        changes=changes,
        vendor_groups=groups,
        context=_context(base.info, head.info),
        unchanged=unchanged,
    )


def headline(a: Attribution) -> list[str]:
    """Human sentences, e.g. '5 changes, all trap:dirty-categorical -> likely ...'."""
    out = []
    c = a.counts()
    total = len(a.changes)
    if total == 0:
        return [f"{a.base_run} -> {a.head_run}: no test outcome changed"]
    parts = ", ".join(f"{n} {k}" for k, n in c.items() if n)
    out.append(f"{a.base_run} -> {a.head_run}: {total} changed test(s) ({parts})")
    vendor = [x for x in a.changes if x.cause == "vendor"]
    if vendor:
        full = [g for g in a.vendor_groups if g.all_changes and g.tag.startswith("trap:")] or [
            g for g in a.vendor_groups if g.all_changes
        ]
        if full:
            g = full[0]
            out.append(f"vendor: {len(vendor)} change(s), all {g.tag} -> {g.hint}")
        elif a.vendor_groups:
            g = a.vendor_groups[0]
            out.append(
                f"vendor: {len(vendor)} change(s), most common {g.tag} ({g.count}) -> {g.hint}"
            )
    for cause in ("hub", "spoke"):
        xs = [x for x in a.changes if x.cause == cause]
        if xs:
            spokes = sorted({x.spoke for x in xs})
            out.append(f"{cause}: {len(xs)} change(s) across {', '.join(spokes)}")
    unk = [x for x in a.changes if x.cause == "unknown"]
    if unk:
        what = sorted({w for x in unk for w in x.changed})
        out.append(f"unknown: {len(unk)} change(s); multiple inputs changed ({', '.join(what)})")
    return out


def render_text(a: Attribution) -> str:
    lines = headline(a)
    if a.context:
        lines.append("context: " + "; ".join(a.context))
    for ch in a.changes:
        lines.append(
            f"  {ch.cause.upper():<7} {ch.spoke}/{ch.test_id}: {ch.before} -> {ch.after}"
            + (f"  [changed: {', '.join(ch.changed)}]" if ch.changed else "")
        )
    return "\n".join(lines) + "\n"
