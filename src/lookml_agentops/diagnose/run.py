"""``lkagent diagnose run``: answer every test through a runner and record a fingerprinted run."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

import duckdb

from lookml_agentops._util.gitinfo import input_version
from lookml_agentops._util.hashing import sha256_obj
from lookml_agentops._util.io import load_yaml
from lookml_agentops.catalog import load_catalog
from lookml_agentops.config import LkagentConfig
from lookml_agentops.diagnose.comparator import TestResult, compare
from lookml_agentops.diagnose.deps import test_deps
from lookml_agentops.diagnose.elements import (
    Element,
    Facet,
    catalog_elements,
    model_elements,
    spec_elements,
)
from lookml_agentops.diagnose.ground_truth import GroundTruth
from lookml_agentops.diagnose.history import History, RunInfo, RunRecord
from lookml_agentops.diagnose.loader import LoadedTest, load_suites, wrap_generated
from lookml_agentops.diagnose.runners.base import AgentContext, Runner
from lookml_agentops.generate.compile import CompiledAgent, compile_agents
from lookml_agentops.inputs.declared import declared_inputs, load_owners
from lookml_agentops.inputs.model import TrackedInput
from lookml_agentops.lookml.resolve import EffectiveModel
from lookml_agentops.spec.discover import discover_specs

DATA_INPUT = "data:warehouse"


def make_runner(name: str, scenario: str, cfg: LkagentConfig) -> Runner:
    if name == "mock":
        from lookml_agentops.diagnose.runners.mock import MockRunner

        return MockRunner(scenario)
    if name == "ca":
        from lookml_agentops.diagnose.runners.ca import CARunner

        return CARunner.from_config(cfg)
    if name == "mcp":
        from lookml_agentops.diagnose.runners.mcp import MCPRunner

        return MCPRunner.from_config(cfg)
    raise ValueError(f"unknown runner {name!r}")


def ensure_seed(cfg: LkagentConfig, log: Callable[[str], None]) -> None:
    db = cfg.path(cfg.seed.duckdb)
    if db.exists():
        return
    from lookml_agentops.seed.run import run_seed

    log(f"seed database {db} not found; generating it (lkagent seed)")
    run_seed(
        seed=cfg.seed.seed,
        as_of=cfg.as_of,
        scale=cfg.seed.scale,
        csv_dir=cfg.path(cfg.seed.out_dir),
        duckdb_path=db,
        manifest_path=None,
    )


def _field_tags(ca: CompiledAgent) -> Callable[[str | None, str], list[str]]:
    def tags(explore: str | None, field: str) -> list[str]:
        be = ca.binding.explore(explore) if explore else None
        bes = [be] if be else ca.binding.explores
        for b in bes:
            f = ca.binding.field_ref(b.name, field)
            if f is not None:
                return f.tags
        return []

    return tags


@dataclass
class RunOptions:
    runner: str = "mock"
    scenario: str = "baseline"
    agents: list[str] | None = None
    label: str | None = None
    record: bool = True
    rebaseline: bool = False
    include_generated: bool = True


def plan_tests(
    cfg: LkagentConfig, agents: dict[str, CompiledAgent], include_generated: bool = True
) -> list[LoadedTest]:
    tests = [lt for lt in load_suites(cfg) if lt.test.agent in agents]
    unknown = sorted({lt.test.agent for lt in load_suites(cfg)} - set(_all_agent_ids(cfg)))
    if unknown:
        raise ValueError(f"suite tests reference unknown agents: {unknown}")
    if include_generated:
        for aid, ca in sorted(agents.items()):
            tests += wrap_generated(ca.tests, aid)
    return tests


def _all_agent_ids(cfg: LkagentConfig) -> list[str]:
    return [s.agent_id for s in discover_specs(cfg)]


def collect_inputs(
    cfg: LkagentConfig, runner: Runner, gt_hashes: dict[str, str], started: dt.datetime
) -> dict[str, TrackedInput]:
    owners = load_owners(cfg)
    inputs = {i.input_id: i for i in declared_inputs(cfg, owners)}
    for sf in discover_specs(cfg):
        iid = f"agent:{sf.agent_id}"
        inputs[iid] = TrackedInput(
            input_id=iid,
            kind="agent_spec",
            version=input_version(sf.path),
            owner=owners.owner(iid, sf.rel),
            path=sf.rel,
        )
    meta = runner.meta
    rid = f"runner:{meta.runner}"
    inputs[rid] = TrackedInput(
        input_id=rid,
        kind="runner",
        version=meta.runner_version,
        owner=owners.owner(rid),
        meta={"scenario": meta.vendor_profile, "date": started.date().isoformat(), **meta.extra},
    )
    manifest = cfg.path(cfg.seed.manifest)
    seed_hash = load_yaml(manifest).get("content_hash") if manifest.exists() else None
    inputs[DATA_INPUT] = TrackedInput(
        input_id=DATA_INPUT,
        kind="data",
        version=sha256_obj(sorted(gt_hashes.items()))[:16],
        owner=owners.owner(DATA_INPUT),
        meta={"ground_truth_results": len(gt_hashes), "seed_manifest": seed_hash},
    )
    return inputs


def _content(
    element_id: str, kind: str, input_id: str, version: str, location: str | None
) -> Element:
    return Element(
        element_id=element_id,
        kind=kind,
        facets={
            "content": Facet(
                version=version,
                input_id=input_id,
                location=location,
                params={"value": {"v": version, "loc": location or ""}},
            ),
        },
    )


def collect_elements(
    cfg: LkagentConfig,
    models: dict[str, EffectiveModel],
    agents: dict[str, CompiledAgent],
    tests: list[LoadedTest],
    runner: Runner,
    gt_hashes: dict[str, str],
) -> dict[str, Element]:
    out = model_elements(models)
    for ca in agents.values():
        out.update(spec_elements(ca.spec))
    entry = cfg.catalog()
    if entry is not None:
        out.update(catalog_elements(load_catalog(cfg), entry[0], entry[1].path))
    for lt in tests:
        out[f"test:{lt.test.id}"] = _content(
            f"test:{lt.test.id}", "test", lt.source, lt.test_hash, lt.location
        )
    meta = runner.meta
    out[f"runner:{meta.runner}"] = _content(
        f"runner:{meta.runner}", "runner", f"runner:{meta.runner}", meta.runner_version, None
    )
    for tid, h in gt_hashes.items():
        out[f"gt:{tid}"] = _content(f"gt:{tid}", "gt", DATA_INPUT, h, None)
    return out


def run_diagnose(
    cfg: LkagentConfig, opts: RunOptions, *, log: Callable[[str], None] = lambda _: None
) -> RunRecord:
    started = dt.datetime.now(dt.UTC).replace(tzinfo=None, microsecond=0)
    agents = compile_agents(cfg)
    if opts.agents:
        missing = sorted(set(opts.agents) - set(agents))
        if missing:
            raise ValueError(f"unknown or abstract agent(s): {missing}")
        agents = {a: agents[a] for a in opts.agents}
    models: dict[str, EffectiveModel] = {}
    for ca in agents.values():
        models.update(ca.models)
    tests = plan_tests(cfg, agents, opts.include_generated)
    runner = make_runner(opts.runner, opts.scenario, cfg)
    catalog = load_catalog(cfg)
    if opts.runner == "mock" or any(lt.sql_path for lt in tests):
        ensure_seed(cfg, log)
    db = cfg.path(cfg.seed.duckdb)
    con = duckdb.connect(str(db), read_only=True) if db.exists() else None
    history = History(cfg.path(cfg.diagnose.history)) if opts.record else None
    if history is not None and history.migration_note:
        log(history.migration_note)
    baselines = history.baselines() if history is not None else {}
    results: list[TestResult] = []
    gt_hashes: dict[str, str] = {}
    runner_element = f"runner:{runner.meta.runner}"
    try:
        truth = GroundTruth(con, cfg.as_of) if con is not None else None
        for lt in tests:
            ca = agents[lt.test.agent]
            ctx = AgentContext(lt.test.agent, ca.spec, ca.binding, cfg.as_of, con)
            ans = runner.answer(lt.test, ctx)
            rows = (
                truth.rows(lt.sql_path) if truth is not None and lt.sql_path is not None else None
            )
            res = compare(
                lt,
                ans,
                as_of=cfg.as_of,
                truth=rows,
                field_tags=_field_tags(ca),
                baseline_gt_hash=None if opts.rebaseline else baselines.get(lt.test.id),
            )
            if res.gt_hash is not None:
                gt_hashes[lt.test.id] = res.gt_hash
            res.deps = [
                d.model_dump()
                for d in test_deps(
                    lt,
                    ans,
                    ca.binding,
                    catalog,
                    runner_element=runner_element,
                    has_truth=rows is not None,
                )
            ]
            results.append(res)
        results.sort(key=lambda r: (r.agent, r.kind != "golden", r.test_id))
        meta = runner.meta
        info = RunInfo(
            started_at=started,
            label=opts.label,
            runner=meta.runner,
            runner_version=meta.runner_version,
            scenario=meta.vendor_profile,
            as_of=cfg.as_of,
            agents=sorted(agents),
        )
        rec = RunRecord(
            info=info,
            inputs=collect_inputs(cfg, runner, gt_hashes, started),
            elements=collect_elements(cfg, models, agents, tests, runner, gt_hashes),
            results=results,
        )
        if history is not None:
            rec.info = history.record(rec)
            new = {t: h for t, h in gt_hashes.items() if opts.rebaseline or t not in baselines}
            history.set_baselines(new, rec.info.run_id)
        return rec
    finally:
        runner.close()
        if con is not None:
            con.close()
        if history is not None:
            history.close()


def explain_deps(rec: RunRecord, test_id: str, cfg: LkagentConfig) -> str:
    """Human-readable dependency set of one test, with owners and file:line provenance."""
    res = rec.result(test_id)
    if res is None:
        raise KeyError(f"test {test_id!r} is not in {rec.info.run_id}")
    owners = load_owners(cfg)
    lines = [f"{test_id} ({res.agent}, {res.status}) depends on:"]
    for d in res.deps:
        el = rec.elements.get(d["element_id"])
        lines.append(
            f"  [{d['rank']}] {d['role']:<9} {d['element_id']}  facets={','.join(d['facets'])}"
        )
        if el is None:
            continue
        for fname in d["facets"]:
            f = el.facets.get(fname)
            if f is None:
                continue
            if el.kind in ("field", "explore"):
                locs = sorted({p["loc"] for p in f.params.values() if p.get("loc")})
                for loc in locs:
                    proj, _, rest = loc.partition("/")
                    iid = f"lookml:{proj}"
                    lines.append(
                        f"        {fname:<8} {iid}  {loc}  owner={owners.owner(iid, rest.rsplit(':', 1)[0])}"
                    )
            else:
                path = (f.location or "").rsplit(":", 1)[0] or None
                lines.append(
                    f"        {fname:<8} {f.input_id}  {f.location or '-'}  owner={owners.owner(f.input_id, path)}"
                    f"  version={f.version}"
                )
    return "\n".join(lines) + "\n"
