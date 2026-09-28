"""``lkagent demo``: the drift story end to end, fully offline.

1. baseline with vendor profile v1;
2. vendor switches to v2_fuzzy_values -> attributed to **vendor**;
3. the hub team changes net revenue to ignore refunds -> attributed to **hub** in both spokes;
4. the finance team redefines its own refund rate -> attributed to **spoke** (finance only).

Everything runs on a scratch copy of the example project; the checked-in example is untouched.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lookml_agentops.attribute.attribute import Attribution, attribute, render_text
from lookml_agentops.config import load_config
from lookml_agentops.report.build import write_reports
from lookml_agentops.report.data import build_report_data
from lookml_agentops.verify.history import History, RunRecord
from lookml_agentops.verify.run import VerifyOptions, ensure_seed, run_verify

HUB_EDIT = (
    "lookml/core_hub/views/orders.view.lkml",
    "sql: ${gross_amount} - ${discount_amount} - COALESCE(${order_refund_facts.refund_amount}, 0) ;;",
    "sql: ${gross_amount} - ${discount_amount} ;;",
)
SPOKE_EDIT = (
    "lookml/finance_spoke/views/finance_refinements.view.lkml",
    "sql: 1.0 * ${order_refund_facts.total_refunds} / NULLIF(${gross_revenue}, 0) ;;",
    "sql: 1.0 * ${order_refund_facts.total_refunds} / NULLIF(${net_revenue}, 0) ;;",
)


@dataclass
class DemoResult:
    workdir: Path
    attributions: dict[str, Attribution] = field(default_factory=dict)
    reports: dict[str, list[Path]] = field(default_factory=dict)
    ok: bool = True
    problems: list[str] = field(default_factory=list)


def _edit(root: Path, edit: tuple[str, str, str], *, revert: bool = False) -> None:
    path = root / edit[0]
    old, new = (edit[2], edit[1]) if revert else (edit[1], edit[2])
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"demo edit target not found in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _expect(res: DemoResult, name: str, att: Attribution, cause: str, spokes: set[str]) -> None:
    causes = {c.cause for c in att.changes}
    got_spokes = {c.spoke for c in att.changes}
    if not att.changes or causes != {cause} or got_spokes != spokes:
        res.ok = False
        res.problems.append(
            f"{name}: expected all changes -> {cause} in {sorted(spokes)}, "
            f"got {sorted(causes)} in {sorted(got_spokes)}"
        )


def run_demo(source: Path, workdir: Path, log: Callable[[str], None] = print) -> DemoResult:
    if workdir.exists():
        shutil.rmtree(workdir)
    root = workdir / "harborline"
    shutil.copytree(source, root, ignore=shutil.ignore_patterns(".lkagent", "reports", "*.wal"))
    cfg = load_config(root)
    ensure_seed(cfg, log)
    res = DemoResult(workdir=workdir)

    def verify(profile: str, label: str) -> RunRecord:
        rec = run_verify(load_config(root), VerifyOptions(profile=profile, label=label))
        passed = sum(r.status == "pass" for r in rec.results)
        log(f"  {rec.info.run_id}  {label:<42} {passed}/{len(rec.results)} pass")
        return rec

    log("step 1  baseline")
    base = verify("v1", "baseline (vendor v1)")
    log("step 2  the vendor changes filter-value matching")
    vendor = verify("v2_fuzzy_values", "vendor switched to fuzzy value matching")
    res.attributions["vendor"] = attribute(base, vendor)
    log(render_text(res.attributions["vendor"]))

    log("step 3  the hub team edits net revenue (drops refunds)")
    _edit(root, HUB_EDIT)
    hub = verify("v1", "hub: net revenue ignores refunds")
    res.attributions["hub"] = attribute(base, hub)
    log(render_text(res.attributions["hub"]))
    _edit(root, HUB_EDIT, revert=True)

    log("step 4  the finance team redefines its refund rate")
    _edit(root, SPOKE_EDIT)
    spoke = verify("v1", "finance spoke: refund rate over net revenue")
    res.attributions["spoke"] = attribute(base, spoke)
    log(render_text(res.attributions["spoke"]))
    _edit(root, SPOKE_EDIT, revert=True)

    with History(cfg.path(cfg.verify.history)) as h:
        for name, head in (("vendor", vendor), ("hub", hub), ("spoke", spoke)):
            data = build_report_data(h, head=head.info.run_id, base=base.info.run_id)
            res.reports[name] = write_reports(data, workdir / "reports" / name)

    _expect(res, "vendor", res.attributions["vendor"], "vendor", {"logistics_spoke"})
    _expect(res, "hub", res.attributions["hub"], "hub", {"finance_spoke", "logistics_spoke"})
    _expect(res, "spoke", res.attributions["spoke"], "spoke", {"finance_spoke"})
    return res
