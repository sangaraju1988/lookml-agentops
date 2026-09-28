"""``lkagent demo``: four attributions end to end, fully offline, on a scratch copy of the example.

1. external — the mock switches to fuzzy filter-value matching; failures are attributed to the
   runner (owner bi-platform), grouped under trap:dirty-categorical; an evidence bundle is written;
2. LookML  — net_revenue's SQL changes in core_project; failures in both the finance and logistics
   agents are attributed to that field and file (owner central-data-platform); ``impact`` predicts
   exactly these tests beforehand;
3. spec    — a non-locked rule in finance-analyst.agent.md is edited; failures are attributed to
   that rule (owner finance-analytics); editing to contradict a locked baseline rule instead is a
   compile error;
4. data    — late-arriving refunds are loaded; the ground truth moves; verdict ``data`` (drift),
   not a regression.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lookml_agentops.config import LkagentConfig, load_config
from lookml_agentops.diagnose.bundle import build_bundle, write_bundle
from lookml_agentops.diagnose.history import History, RunRecord
from lookml_agentops.diagnose.impact import ImpactReport, analyze
from lookml_agentops.diagnose.run import RunOptions, ensure_seed, run_diagnose
from lookml_agentops.diagnose.why import Diagnosis, diagnose, headline
from lookml_agentops.generate.compile import CompileError, compile_agents
from lookml_agentops.inputs.declared import load_owners
from lookml_agentops.report.build import write_reports
from lookml_agentops.report.data import build_report_data
from lookml_agentops.seed.run import run_seed

LOOKML_EDIT = (
    "projects/core_project/views/orders.view.lkml",
    "sql: ${gross_amount} - ${discount_amount} - COALESCE(${order_refund_facts.refund_amount}, 0) ;;",
    "sql: ${gross_amount} - ${discount_amount} ;;",
)
RULE_EDIT = (
    "agents/finance-analyst.agent.md",
    'text: \'"Sales" and "net sales" mean net revenue.\'',
    'text: \'"Sales" and "net sales" mean gross revenue.\'',
)
LOCKED_EDIT = (
    "agents/finance-analyst.agent.md",
    "- id: quarter-is-fiscal",
    "- id: finance-revenue\n  text: '\"Revenue\" means gross revenue.'\n- id: quarter-is-fiscal",
)


@dataclass
class DemoResult:
    workdir: Path
    runs: dict[str, RunRecord] = field(default_factory=dict)
    diagnoses: dict[str, Diagnosis] = field(default_factory=dict)
    impact: ImpactReport | None = None
    bundle: Path | None = None
    locked_error: str | None = None
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


def _check(res: DemoResult, cond: bool, message: str) -> None:
    if not cond:
        res.ok = False
        res.problems.append(message)


def run_demo(source: Path, workdir: Path, log: Callable[[str], None] = print) -> DemoResult:
    if workdir.exists():
        shutil.rmtree(workdir)
    root = workdir / "harborline"
    shutil.copytree(source, root, ignore=shutil.ignore_patterns(".lkagent", "reports", "*.wal"))
    base_snapshot = workdir / "base"
    shutil.copytree(root, base_snapshot, ignore=shutil.ignore_patterns("seed"))
    res = DemoResult(workdir=workdir)
    owners = load_owners(load_config(root))

    def cfg() -> LkagentConfig:
        return load_config(root)

    ensure_seed(cfg(), log)

    def run(name: str, scenario: str = "baseline") -> RunRecord:
        rec = run_diagnose(cfg(), RunOptions(scenario=scenario, label=name))
        ok = sum(r.status == "pass" for r in rec.results)
        log(f"  {rec.info.run_id}  {name:<46} {ok}/{len(rec.results)} pass")
        res.runs[name] = rec
        return rec

    def why(name: str, a: RunRecord, b: RunRecord) -> Diagnosis:
        d = diagnose(a, b, owners)
        res.diagnoses[name] = d
        for line in headline(d):
            log(f"    {line}")
        return d

    log("step 0  baseline")
    base = run("baseline")

    log("step 1  external: the vendor starts fuzzy-matching filter values")
    ext = run("external: fuzzy value matching", "fuzzy_values")
    d1 = why("external", base, ext)
    res.bundle = write_bundle(
        workdir / "bundles" / f"{base.info.run_id}-{ext.info.run_id}.zip",
        build_bundle(base, ext, d1),
    )
    log(f"    evidence bundle: {res.bundle}")
    _check(
        res,
        bool(d1.verdicts) and {v.cause for v in d1.verdicts} == {"external"},
        "external: causes",
    )
    _check(res, {v.owner for v in d1.verdicts} == {"bi-platform"}, "external: owner")
    _check(
        res,
        any(g.tag == "trap:dirty-categorical" and g.all_changes for g in d1.external_groups),
        "external: grouping",
    )

    log("step 2  LookML: core_project changes net_revenue's SQL (drops refunds)")
    _edit(root, LOOKML_EDIT)
    res.impact = analyze(load_config(base_snapshot), cfg(), base_label="base", head_label="edited")
    predicted = res.impact.test_ids()
    log(
        f"    impact predicts {len(predicted)} affected test(s) in {', '.join(sorted(res.impact.affected))}"
    )
    lookml = run("lookml: net_revenue ignores refunds")
    d2 = why("lookml", base, lookml)
    _edit(root, LOOKML_EDIT, revert=True)
    failed = {v.test_id for v in d2.verdicts}
    _check(
        res,
        failed == predicted,
        f"lookml: impact predicted {sorted(predicted)} but {sorted(failed)} changed",
    )
    _check(res, {v.input_id for v in d2.verdicts} == {"lookml:core_project"}, "lookml: input")
    _check(res, {v.owner for v in d2.verdicts} == {"central-data-platform"}, "lookml: owner")
    _check(
        res,
        {v.agent for v in d2.verdicts} == {"finance-analyst", "logistics-ops"},
        "lookml: agents",
    )
    _check(
        res,
        all(
            "orders.net_revenue" in (v.likely_cause or "")
            and "orders.view.lkml:87" in (v.likely_cause or "")
            for v in d2.verdicts
        ),
        "lookml: field-level cause",
    )

    log("step 3  spec: finance-analyst edits a non-locked rule ('sales' -> gross revenue)")
    _edit(root, RULE_EDIT)
    spec = run("spec: sales means gross revenue")
    d3 = why("spec", base, spec)
    _edit(root, RULE_EDIT, revert=True)
    _check(
        res,
        bool(d3.verdicts) and {v.input_id for v in d3.verdicts} == {"agent:finance-analyst"},
        "spec: input",
    )
    _check(res, {v.owner for v in d3.verdicts} == {"finance-analytics"}, "spec: owner")
    _check(
        res,
        all("rule:sales-means-net" in (v.likely_cause or "") for v in d3.verdicts),
        "spec: rule-level cause",
    )
    log("        ...and an edit that contradicts a locked baseline rule instead:")
    _edit(root, LOCKED_EDIT)
    try:
        compile_agents(cfg(), "finance-analyst")
        _check(res, False, "spec: locked edit compiled")
    except CompileError as exc:
        res.locked_error = str(exc).splitlines()[0]
        log(f"    compile error: {res.locked_error}")
        _check(res, "LKS008" in str(exc), "spec: locked error id")
    _edit(root, LOCKED_EDIT, revert=True)

    log("step 4  data: late-arriving refunds land in the warehouse")
    c = cfg()
    run_seed(
        seed=c.seed.seed,
        as_of=c.as_of,
        scale=c.seed.scale,
        csv_dir=c.path(c.seed.out_dir),
        duckdb_path=c.path(c.seed.duckdb),
        manifest_path=c.path(c.seed.manifest),
        deltas=["late-refunds"],
    )
    data = run("data: late-arriving refunds")
    d4 = why("data", base, data)
    _check(res, bool(d4.verdicts) and {v.cause for v in d4.verdicts} == {"data"}, "data: causes")
    _check(res, {v.after for v in d4.verdicts} == {"drift"}, "data: drift, not failure")
    _check(res, {v.owner for v in d4.verdicts} == {"data-engineering"}, "data: owner")

    c = cfg()
    with History(c.path(c.diagnose.history)) as h:
        for name, rec in (("external", ext), ("lookml", lookml), ("spec", spec), ("data", data)):
            rd = build_report_data(h, owners, head=rec.info.run_id, base=base.info.run_id)
            res.reports[name] = write_reports(rd, workdir / "reports" / name)
    for name, paths in res.reports.items():
        log(f"  {name:<9} report: {paths[-1]}")
    return res
