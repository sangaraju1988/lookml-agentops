"""Terminal summary for a verify run."""

from __future__ import annotations

from collections import Counter

from lookml_agentops.verify.history import RunRecord

STATUSES = ("pass", "degraded", "fail", "error", "skipped")


def counts_by_spoke(rec: RunRecord) -> dict[str, Counter[str]]:
    out: dict[str, Counter[str]] = {}
    for r in rec.results:
        out.setdefault(r.spoke, Counter())[r.status] += 1
    return out


def render_summary(rec: RunRecord, *, show: int = 50) -> str:
    i = rec.info
    lines = [
        f"{i.run_id or '(not recorded)'}  runner={i.runner_version}  mode={i.mode}  as_of={i.as_of}",
    ]
    for spoke, c in sorted(counts_by_spoke(rec).items()):
        total = sum(c.values())
        rate = 100.0 * c["pass"] / total if total else 0.0
        parts = "  ".join(f"{s}={c[s]}" for s in STATUSES if c[s])
        lines.append(f"  {spoke:<18} {rate:5.1f}% pass  ({parts})")
    bad = [r for r in rec.results if r.status not in ("pass", "skipped")]
    for r in bad[:show]:
        lines.append(f"  {r.status.upper():<8} {r.spoke}/{r.test_id}: {'; '.join(r.details)}")
    if len(bad) > show:
        lines.append(f"  ... {len(bad) - show} more")
    return "\n".join(lines) + "\n"
