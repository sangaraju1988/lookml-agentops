"""``lkagent verify`` orchestration."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

import duckdb

from lookml_agentops._util.gitinfo import project_revision
from lookml_agentops._util.io import load_yaml
from lookml_agentops.compile.build import CompiledAgent, compile_agents
from lookml_agentops.config import LkagentConfig
from lookml_agentops.lookml.resolve import EffectiveModel
from lookml_agentops.verify.comparator import TestResult, compare
from lookml_agentops.verify.ground_truth import GroundTruth
from lookml_agentops.verify.history import History, RunInfo, RunRecord
from lookml_agentops.verify.loader import LoadedTest, load_golden, wrap_generated
from lookml_agentops.verify.runners.base import Runner, SpokeContext


def make_runner(name: str, profile: str) -> Runner:
    if name == "mock":
        from lookml_agentops.verify.runners.mock import MockRunner

        return MockRunner(profile)
    if name == "ca":
        from lookml_agentops.verify.runners.ca import CARunner

        return CARunner.from_env()
    if name == "mcp":
        from lookml_agentops.verify.runners.mcp import MCPRunner

        return MCPRunner.from_env()
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


def _field_tags(em: EffectiveModel) -> Callable[[str | None, str], list[str]]:
    def tags(explore: str | None, field: str) -> list[str]:
        alias, _, name = field.partition(".")
        views = em.explore_views(em.explores[explore]) if explore in em.explores else {}
        view = views.get(alias) or em.views.get(alias)
        f = view.fields.get(name) if view else None
        return f.tags if f else []

    return tags


@dataclass
class VerifyOptions:
    runner: str = "mock"
    profile: str = "v1"
    mode: str = "nightly"
    spokes: list[str] | None = None
    label: str | None = None
    include_adherence: bool = True
    record: bool = True


def run_verify(
    cfg: LkagentConfig,
    opts: VerifyOptions,
    *,
    golden_cfg: LkagentConfig | None = None,
    log: Callable[[str], None] = lambda _: None,
) -> RunRecord:
    """Run golden + adherence tests. ``golden_cfg`` supplies golden files/seed (defaults to cfg)."""
    gcfg = golden_cfg or cfg
    runner = make_runner(opts.runner, opts.profile)
    spokes = opts.spokes or cfg.spokes
    agents: dict[str, CompiledAgent] = compile_agents(cfg)
    golden = load_golden(gcfg)
    con: duckdb.DuckDBPyConnection | None = None
    if opts.runner == "mock":
        ensure_seed(gcfg, log)
    db = gcfg.path(gcfg.seed.duckdb)
    if db.exists():
        con = duckdb.connect(str(db), read_only=True)
    results: list[TestResult] = []
    try:
        truth = GroundTruth(con, cfg.as_of) if con is not None else None
        for spoke in spokes:
            agent = agents[spoke]
            ctx = SpokeContext(spoke, agent.instructions, agent.model, cfg.as_of, con)
            tests: list[LoadedTest] = [lt for lt in golden if lt.test.spoke == spoke]
            if opts.include_adherence:
                tests += wrap_generated(agent.tests)
            tag_fn = _field_tags(agent.model)
            for lt in tests:
                ans = runner.answer(lt.test, ctx)
                rows = (
                    truth.rows(lt.sql_path)
                    if truth is not None and lt.sql_path is not None
                    else None
                )
                results.append(compare(lt, ans, as_of=cfg.as_of, truth=rows, field_tags=tag_fn))
    finally:
        runner.close()
        if con is not None:
            con.close()

    manifest = gcfg.path(gcfg.seed.manifest)
    data_hash = str(load_yaml(manifest).get("content_hash")) if manifest.exists() else None
    meta = runner.meta
    info = RunInfo(
        started_at=dt.datetime.now(dt.UTC).replace(tzinfo=None, microsecond=0),
        label=opts.label,
        mode=opts.mode,
        runner=meta.runner,
        runner_version=meta.runner_version,
        vendor_profile=meta.vendor_profile,
        as_of=cfg.as_of,
        hub=cfg.hub,
        revisions={
            p: project_revision(p, cfg.project_path(p)).model_dump(exclude={"project"})
            for p in sorted(cfg.projects)
        },
        instruction_hashes={
            s: {"content": a.instructions.content_hash, "layers": a.instructions.layer_hashes()}
            for s, a in sorted(agents.items())
        },
        data_hash=data_hash,
    )
    results.sort(key=lambda r: (r.spoke, r.kind != "golden", r.test_id))
    if opts.record:
        with History(gcfg.path(gcfg.verify.history)) as h:
            info = h.record(info, results)
    return RunRecord(info=info, results=results)
