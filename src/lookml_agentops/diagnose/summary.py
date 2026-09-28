"""Terminal summary for a diagnose run."""

from __future__ import annotations

from collections import Counter

from lookml_agentops.diagnose.history import RunRecord

STATUSES = ("pass", "degraded", "drift", "fail", "error", "skipped")


def render_summary(rec: RunRecord, *, show: int = 40) -> str:
    i = rec.info
    lines = [f"{i.run_id or '(not recorded)'}  runner={i.runner_version}  as_of={i.as_of}"]
    by_agent: dict[str, Counter[str]] = {}
    for r in rec.results:
        by_agent.setdefault(r.agent, Counter())[r.status] += 1
    for agent, c in sorted(by_agent.items()):
        checkable = sum(v for k, v in c.items() if k != "skipped")
        rate = 100.0 * (c["pass"] + c["drift"]) / checkable if checkable else 0.0
        parts = "  ".join(f"{s}={c[s]}" for s in STATUSES if c[s])
        lines.append(f"  {agent:<20} {rate:5.1f}% pass  ({parts})")
    bad = [r for r in rec.results if r.status not in ("pass", "skipped")]
    for r in bad[:show]:
        lines.append(f"  {r.status.upper():<8} {r.agent}/{r.test_id}: {'; '.join(r.details)}")
    if len(bad) > show:
        lines.append(f"  ... {len(bad) - show} more")
    return "\n".join(lines) + "\n"
