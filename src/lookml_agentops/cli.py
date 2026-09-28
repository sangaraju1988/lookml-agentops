"""``lkagent`` command-line interface.

Two modes:

* ``lkagent generate …`` — author, validate, compile and deploy agent instructions;
* ``lkagent diagnose …`` — "the AI agent's answer changed: why, and whose problem is it?"
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from lookml_agentops import __version__
from lookml_agentops.config import ConfigError, LkagentConfig, load_config

app = typer.Typer(
    name="lkagent",
    help="Author and diagnose Looker AI agents as code.",
    no_args_is_help=True,
    add_completion=False,
)
generate_app = typer.Typer(
    help="Author, validate, compile and deploy agent instructions.", no_args_is_help=True
)
diagnose_app = typer.Typer(
    help="The agent's answer changed: why, and whose problem is it?", no_args_is_help=True
)
app.add_typer(generate_app, name="generate")
app.add_typer(diagnose_app, name="diagnose")

ConfigOpt = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="Path to lkagent.yaml (default: search upward)."),
]


def _cfg(config: Path | None) -> LkagentConfig:
    try:
        return load_config(config)
    except (FileNotFoundError, ConfigError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
def seed(
    config: ConfigOpt = None,
    scale: Annotated[float | None, typer.Option(help="Override seed.scale")] = None,
    no_duckdb: Annotated[bool, typer.Option("--no-duckdb", help="Only write CSVs")] = False,
    delta: Annotated[
        list[str] | None,
        typer.Option(help="Apply a seed delta (late-refunds) on top of the base data"),
    ] = None,
) -> None:
    """Generate the deterministic Harborline dataset (CSVs + DuckDB)."""
    from lookml_agentops.seed.run import run_seed

    cfg = _cfg(config)
    manifest = run_seed(
        seed=cfg.seed.seed,
        as_of=cfg.as_of,
        scale=scale if scale is not None else cfg.seed.scale,
        csv_dir=cfg.path(cfg.seed.out_dir),
        duckdb_path=None if no_duckdb else cfg.path(cfg.seed.duckdb),
        manifest_path=cfg.path(cfg.seed.manifest),
        deltas=list(delta) if delta else None,
    )
    width = max(len(n) for n in manifest.tables)
    for name, stat in sorted(manifest.tables.items()):
        typer.echo(f"  {name:<{width}}  {stat.rows:>8,} rows  {stat.sha256[:12]}")
    typer.echo(f"content_hash: {manifest.content_hash}")


@app.command()
def graph(
    config: ConfigOpt = None,
    fmt: Annotated[str, typer.Option("--format", "-f", help="text | mermaid | json")] = "text",
    project: Annotated[
        str | None, typer.Option(help="Also print the effective model of this project")
    ] = None,
) -> None:
    """Print the LookML import graph (any topology) and optionally a project's effective model."""
    from lookml_agentops._util.hashing import canonical_json
    from lookml_agentops.lookml.graph import (
        effective_model_dict,
        render_fields_text,
        render_mermaid,
        render_text,
    )
    from lookml_agentops.lookml.resolve import ResolveError, load_workspace, resolve_project

    cfg = _cfg(config)
    try:
        ws = load_workspace(cfg)
        if fmt == "json":
            names = (
                [project]
                if project
                else sorted(p for p in cfg.projects if ws.projects[p].model_files)
            )
            payload = {p: effective_model_dict(resolve_project(ws, p)) for p in names}
            typer.echo(canonical_json(payload), nl=False)
            return
        typer.echo(render_mermaid(ws) if fmt == "mermaid" else render_text(ws), nl=False)
        if project:
            em = resolve_project(ws, project)
            typer.echo(f"\neffective model: {project}")
            typer.echo(render_fields_text(em), nl=False)
            for problem in em.problems:
                typer.echo(f"problem: {problem}", err=True)
    except ResolveError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc


@generate_app.command("lint")
def generate_lint(
    config: ConfigOpt = None,
    fmt: Annotated[str, typer.Option("--format", "-f", help="text | markdown | sarif")] = "text",
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write to file")] = None,
    sarif_base: Annotated[
        Path | None, typer.Option(help="Directory SARIF URIs are relative to (default: cwd)")
    ] = None,
) -> None:
    """Validate LookML, the glossary and agent specs. Exits 1 on any error-level finding."""
    from lookml_agentops._util.hashing import canonical_json
    from lookml_agentops._util.io import write_text
    from lookml_agentops.lint.engine import run_lint
    from lookml_agentops.lint.reporters.markdown import render_markdown
    from lookml_agentops.lint.reporters.sarif import render_sarif
    from lookml_agentops.lint.reporters.text import render_text

    cfg = _cfg(config)
    result = run_lint(cfg)
    if fmt == "sarif":
        text = canonical_json(render_sarif(result, cfg, sarif_base or Path.cwd()))
    elif fmt == "markdown":
        text = render_markdown(result)
    else:
        text = render_text(result)
    if output:
        write_text(output, text)
        typer.echo(f"wrote {output} ({result.errors} error(s), {result.warnings} warning(s))")
    else:
        typer.echo(text, nl=False)
    if result.errors:
        raise typer.Exit(1)


@generate_app.command("new")
def generate_new(
    agent: Annotated[str, typer.Argument(help="New agent id, e.g. finance-analyst")],
    config: ConfigOpt = None,
    description: Annotated[
        str | None,
        typer.Option("--description", "-d", help="Plain-English description (prompted if omitted)"),
    ] = None,
    explore: Annotated[
        list[str] | None, typer.Option(help="Explore as <project>::<explore> (repeatable)")
    ] = None,
    extends: Annotated[
        list[str] | None, typer.Option(help="Spec to extend, relative to the new file")
    ] = None,
    provider: Annotated[str, typer.Option(help="stub | command (LKAGENT_LLM_COMMAND)")] = "stub",
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Default: agents/<agent>.agent.md")
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file")] = False,
) -> None:
    """Draft a *.agent.md with an LLM (or the offline stub), grounded in the model; then lint it."""
    from lookml_agentops.generate.compile import bind_all
    from lookml_agentops.generate.draft import (
        CommandProvider,
        DraftError,
        DraftRequest,
        StubProvider,
        draft_spec,
    )

    cfg = _cfg(config)
    desc = description or typer.prompt("Describe the agent (audience, definitions, exclusions)")
    explores = list(explore or [])
    if not explores:
        explores = [
            e.strip()
            for e in typer.prompt("Explores (<project>::<explore>, comma-separated)").split(",")
        ]
    out = output or cfg.path(f"agents/{agent}.agent.md")
    try:
        prov = StubProvider() if provider == "stub" else CommandProvider()
        path = draft_spec(
            cfg, DraftRequest(agent, desc, explores, list(extends or [])), prov, out, force=force
        )
    except DraftError as exc:
        typer.echo(f"generate new: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"wrote {path} (draft from the {prov.name} provider; review before committing)")
    findings = [f for f in bind_all(cfg).findings if f.file.endswith(path.name)]
    for f in findings:
        typer.echo(f"  {f.severity.upper():<7} {f.rule_id}  {f.file}:{f.line}  {f.message}")
    typer.echo(f"lint: {len(findings)} finding(s) in the draft")


@generate_app.command("compile")
def generate_compile(
    config: ConfigOpt = None,
    agent: Annotated[str | None, typer.Option(help="Compile only this agent id")] = None,
    check: Annotated[
        bool, typer.Option("--check", help="Fail if the committed build/ is stale")
    ] = False,
) -> None:
    """Compile *.agent.md specs to agent_spec.v1 JSON, CA API context, Looker UI text and tests."""
    from lookml_agentops.generate.compile import CompileError, check_build, write_build

    cfg = _cfg(config)
    try:
        if check:
            stale = check_build(cfg)
            if stale:
                typer.echo("stale build artifacts (run `lkagent generate compile`):", err=True)
                for s in stale:
                    typer.echo(f"  {s}", err=True)
                raise typer.Exit(1)
            typer.echo("build artifacts are up to date")
            return
        result = write_build(cfg, agent=agent)
    except CompileError as exc:
        typer.echo(f"compile failed:\n{exc}", err=True)
        raise typer.Exit(1) from exc
    for w in result.warnings:
        typer.echo(f"warning: {w}", err=True)
    for aid, ca in sorted(result.agents.items()):
        typer.echo(
            f"{aid}: {len(ca.spec.active_rules())} rules, {len(ca.tests)} tests, "
            f"content_hash={ca.spec.content_hash[:12]}"
        )
    typer.echo(f"wrote {len(result.files)} files to {result.out_dir}")


@generate_app.command("resolve-golden")
def generate_resolve_golden(config: ConfigOpt = None) -> None:
    """Resolve golden-query Explore URLs to Looker queries (cached in build/golden_cache.json)."""
    from lookml_agentops.generate.golden_resolve import GoldenResolveError, resolve_golden

    cfg = _cfg(config)
    try:
        report = resolve_golden(cfg)
    except GoldenResolveError as exc:
        typer.echo(f"resolve-golden: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"resolved {len(report['resolved'])} URL(s); {len(report['cached'])} already cached")


@generate_app.command("deploy")
def generate_deploy(
    agent: Annotated[str, typer.Argument(help="Agent id to deploy")],
    config: ConfigOpt = None,
) -> None:
    """Stage compiled context on the CA data agent and test it there ([ca]). Publishing is manual."""
    from lookml_agentops.generate.deploy import DeployError, client_from_env, deploy, staging_runner

    cfg = _cfg(config)
    try:
        client = client_from_env(cfg)
        try:
            res = deploy(cfg, agent, client=client, run_against_staging=staging_runner)
        finally:
            client.close()
    except DeployError as exc:
        typer.echo(f"deploy: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(
        f"{agent}: staged (context {res.context_hash}); pass rate {res.pass_rate:.1%} "
        f"(threshold {res.threshold:.1%}); ready to publish: {'yes' if res.ready_to_publish else 'no'}"
    )
    for n in res.notes:
        typer.echo(f"  {n}")
    if not res.ready_to_publish:
        raise typer.Exit(1)


@generate_app.command("rollback")
def generate_rollback(
    agent: Annotated[str, typer.Argument(help="Agent id")],
    config: ConfigOpt = None,
) -> None:
    """Explain how to roll back (manual, in the UI) and list this agent's recorded deployments."""
    from lookml_agentops.generate.deploy import MANUAL_ROLLBACK, deployment_history

    cfg = _cfg(config)
    typer.echo(MANUAL_ROLLBACK)
    history = deployment_history(cfg, agent)
    if not history:
        typer.echo(f"no deployments of {agent} recorded in .lkagent/deployments.json")
        return
    typer.echo(f"recorded deployments of {agent} (newest last):")
    for d in history:
        ready = "ready to publish" if d.get("ready_to_publish") else "below threshold"
        typer.echo(
            f"  {d['at']}  spec {d['spec_hash'][:12]}  context {d['context_hash']}  "
            f"pass {d['pass_rate']:.1%}  {ready}  run {d['run_id']}"
        )


@diagnose_app.command("run")
def diagnose_run(
    config: ConfigOpt = None,
    agent: Annotated[list[str] | None, typer.Option(help="Limit to these agent ids")] = None,
    runner: Annotated[str, typer.Option(help="mock | ca | mcp")] = "",
    scenario: Annotated[str, typer.Option(help="Mock external scenario")] = "",
    label: Annotated[str | None, typer.Option(help="Free-text label stored with the run")] = None,
    suites_only: Annotated[
        bool, typer.Option("--suites-only", help="Skip generated tests")
    ] = False,
    rebaseline: Annotated[
        bool, typer.Option("--rebaseline", help="Accept today's ground truth as the new baseline")
    ] = False,
    explain_deps: Annotated[
        str | None, typer.Option("--explain-deps", help="Print the dependency set of this test id")
    ] = None,
    no_record: Annotated[
        bool, typer.Option("--no-record", help="Do not write run history")
    ] = False,
    fail_on: Annotated[str, typer.Option(help="fail | degraded | never")] = "fail",
) -> None:
    """Answer every test through a runner and record a fingerprinted run."""
    from lookml_agentops.diagnose.run import RunOptions, run_diagnose
    from lookml_agentops.diagnose.run import explain_deps as explain
    from lookml_agentops.diagnose.runners.ca import RunnerConfigError
    from lookml_agentops.diagnose.summary import render_summary
    from lookml_agentops.generate.compile import CompileError

    cfg = _cfg(config)
    opts = RunOptions(
        runner=runner or cfg.diagnose.runner,
        scenario=scenario or cfg.diagnose.scenario,
        agents=list(agent) if agent else None,
        label=label,
        record=not no_record,
        rebaseline=rebaseline,
        include_generated=not suites_only,
    )
    try:
        rec = run_diagnose(cfg, opts, log=lambda m: typer.echo(m, err=True))
    except (CompileError, RunnerConfigError, ValueError) as exc:
        typer.echo(f"diagnose run: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(render_summary(rec), nl=False)
    if explain_deps:
        try:
            typer.echo(explain(rec, explain_deps, cfg), nl=False)
        except KeyError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc
    bad = {"fail", "error"} | ({"degraded"} if fail_on == "degraded" else set())
    if fail_on != "never" and any(r.status in bad for r in rec.results):
        raise typer.Exit(1)


def _two_runs(cfg: LkagentConfig, run_a: str | None, run_b: str | None):  # type: ignore[no-untyped-def]
    from lookml_agentops.diagnose.history import History

    with History(cfg.path(cfg.diagnose.history)) as h:
        ids = h.run_ids()
        if not (run_a and run_b) and len(ids) < 2:
            typer.echo("need at least two recorded runs (lkagent diagnose run)", err=True)
            raise typer.Exit(2)
        b_id = run_b or ids[-1]
        try:
            a_id = run_a or ids[ids.index(b_id) - 1]
            return h.load(a_id), h.load(b_id)
        except (KeyError, ValueError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc


@diagnose_app.command("why")
def diagnose_why(
    run_a: Annotated[
        str | None, typer.Argument(help="Earlier run (default: the one before RUN_B)")
    ] = None,
    run_b: Annotated[str | None, typer.Argument(help="Later run (default: latest)")] = None,
    config: ConfigOpt = None,
    fmt: Annotated[str, typer.Option("--format", "-f", help="text | json")] = "text",
) -> None:
    """Attribute every changed test outcome to an input and its owner."""
    from lookml_agentops._util.hashing import canonical_json
    from lookml_agentops.diagnose.why import diagnose, render_text
    from lookml_agentops.inputs.declared import load_owners

    cfg = _cfg(config)
    a, b = _two_runs(cfg, run_a, run_b)
    d = diagnose(a, b, load_owners(cfg))
    typer.echo(
        canonical_json(d.model_dump(mode="json")) if fmt == "json" else render_text(d), nl=False
    )


@diagnose_app.command("impact")
def diagnose_impact(
    config: ConfigOpt = None,
    base: Annotated[
        str | None, typer.Option(help="Git ref of the base state (monorepo layout)")
    ] = None,
    head: Annotated[
        str | None, typer.Option(help="Git ref of the head state (default: working tree)")
    ] = None,
    base_config: Annotated[
        Path | None, typer.Option(help="lkagent.yaml of a base checkout (instead of --base)")
    ] = None,
    run: Annotated[
        bool, typer.Option("--run", help="Run only the affected tests on the head state")
    ] = False,
    fmt: Annotated[str, typer.Option("--format", "-f", help="text | json")] = "text",
) -> None:
    """Predict which agents and tests a change affects (any project, spec, catalog or suite)."""
    import contextlib

    from lookml_agentops._util.hashing import canonical_json
    from lookml_agentops.diagnose.impact import ImpactError, analyze, config_at_ref, render_text
    from lookml_agentops.diagnose.run import RunOptions, run_diagnose
    from lookml_agentops.diagnose.summary import render_summary
    from lookml_agentops.generate.compile import CompileError

    cfg = _cfg(config)
    if not base and not base_config:
        typer.echo("give --base REF or --base-config PATH", err=True)
        raise typer.Exit(2)
    try:
        with contextlib.ExitStack() as stack:
            base_cfg = (
                _cfg(base_config)
                if base_config
                else stack.enter_context(config_at_ref(cfg, base or "HEAD"))
            )
            head_cfg = stack.enter_context(config_at_ref(cfg, head)) if head else cfg
            report = analyze(
                base_cfg,
                head_cfg,
                base_label=base or str(base_config),
                head_label=head or "working tree",
            )
            typer.echo(
                canonical_json(report.model_dump(mode="json"))
                if fmt == "json"
                else render_text(report),
                nl=False,
            )
            if run and report.test_ids():
                rec = run_diagnose(
                    head_cfg, RunOptions(tests=report.test_ids(), label=f"impact {report.base}")
                )
                typer.echo(render_summary(rec), nl=False)
    except (ImpactError, CompileError) as exc:
        typer.echo(f"diagnose impact: {exc}", err=True)
        raise typer.Exit(2) from exc


@diagnose_app.command("bundle")
def diagnose_bundle(
    run_a: Annotated[str, typer.Argument(help="Run before the change")],
    run_b: Annotated[str, typer.Argument(help="Run after the change")],
    config: ConfigOpt = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Zip path")] = None,
    all_changes: Annotated[
        bool, typer.Option("--all", help="Include non-external verdicts too")
    ] = False,
) -> None:
    """Write a vendor-support evidence zip (structure and fingerprints only, no row data)."""
    from lookml_agentops.diagnose.bundle import build_bundle, write_bundle
    from lookml_agentops.diagnose.why import diagnose
    from lookml_agentops.inputs.declared import load_owners

    cfg = _cfg(config)
    a, b = _two_runs(cfg, run_a, run_b)
    d = diagnose(a, b, load_owners(cfg))
    path = output or cfg.path(f".lkagent/bundles/{run_a}-{run_b}.zip")
    write_bundle(path, build_bundle(a, b, d, only_external=not all_changes))
    n = sum(1 for v in d.verdicts if all_changes or v.cause == "external")
    typer.echo(f"wrote {path} ({n} test(s))")


@diagnose_app.command("report")
def diagnose_report(
    config: ConfigOpt = None,
    run: Annotated[str | None, typer.Option(help="Run to report on (default: latest)")] = None,
    base: Annotated[
        str | None, typer.Option(help="Attribution base (default: previous run)")
    ] = None,
    trend: Annotated[int, typer.Option(help="Number of runs in the trend")] = 10,
    fmt: Annotated[str, typer.Option("--format", "-f", help="md | html | both")] = "both",
    out_dir: Annotated[
        Path | None, typer.Option(help="Output directory (default: reports/)")
    ] = None,
) -> None:
    """Write report.md (PR-comment sized) and/or a self-contained report.html, grouped by owner."""
    from lookml_agentops.diagnose.history import History
    from lookml_agentops.inputs.declared import load_owners
    from lookml_agentops.report.build import write_reports
    from lookml_agentops.report.data import build_report_data

    cfg = _cfg(config)
    formats = ("md", "html") if fmt == "both" else (fmt,)
    with History(cfg.path(cfg.diagnose.history)) as h:
        try:
            data = build_report_data(h, load_owners(cfg), head=run, base=base, trend=trend)
        except (ValueError, KeyError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc
    for p in write_reports(data, out_dir or cfg.path("reports"), formats):
        typer.echo(f"wrote {p}")


@app.command()
def demo(
    workdir: Annotated[Path, typer.Option(help="Scratch directory (recreated)")] = Path(
        "lkagent-demo"
    ),
    source: Annotated[
        Path | None,
        typer.Option(help="Example project to copy (default: bundled Harborline example)"),
    ] = None,
) -> None:
    """Offline demo: external, LookML, spec and data changes, each attributed with an owner."""
    from lookml_agentops.demo import run_demo

    res = run_demo(source or _find_example(), workdir.resolve(), log=typer.echo)
    if not res.ok:
        for p in res.problems:
            typer.echo(f"demo check failed: {p}", err=True)
        raise typer.Exit(1)
    typer.echo(
        "demo OK: external, LookML, spec and data changes were attributed to the right owners"
    )


def _find_example() -> Path:
    for d in (Path.cwd(), *Path.cwd().parents):
        cand = d / "examples" / "harborline" / "lkagent.yaml"
        if cand.exists():
            return cand.parent
    typer.echo("could not find examples/harborline; pass --source", err=True)
    raise typer.Exit(2)


if __name__ == "__main__":  # pragma: no cover
    app()
