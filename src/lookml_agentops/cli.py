"""``lkagent`` command-line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from lookml_agentops import __version__
from lookml_agentops.config import LkagentConfig, load_config

app = typer.Typer(
    name="lkagent",
    help="Lint, compile, verify and attribute Looker AI-agent instructions as code.",
    no_args_is_help=True,
    add_completion=False,
)

ConfigOpt = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="Path to lkagent.yaml (default: search upward)."),
]


def _cfg(config: Path | None) -> LkagentConfig:
    try:
        return load_config(config)
    except FileNotFoundError as exc:
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
    )
    width = max(len(n) for n in manifest.tables)
    for name, stat in sorted(manifest.tables.items()):
        typer.echo(f"  {name:<{width}}  {stat.rows:>8,} rows  {stat.sha256[:12]}")
    typer.echo(f"content_hash: {manifest.content_hash}")


@app.command()
def graph(
    config: ConfigOpt = None,
    fmt: Annotated[str, typer.Option("--format", "-f", help="text | mermaid | json")] = "text",
    spoke: Annotated[
        str | None, typer.Option(help="Also print the effective model of this project")
    ] = None,
) -> None:
    """Print the project import graph and (optionally) a project's effective model."""
    from lookml_agentops._util.hashing import canonical_json
    from lookml_agentops.lookml.graph import (
        effective_model_dict,
        render_fields_text,
        render_mermaid,
        render_text,
    )
    from lookml_agentops.lookml.resolve import load_workspace, resolve_project

    cfg = _cfg(config)
    ws = load_workspace(cfg)
    if fmt == "json":
        payload = {p: effective_model_dict(resolve_project(ws, p)) for p in sorted(cfg.projects)}
        if spoke:
            payload = {spoke: payload[spoke]}
        typer.echo(canonical_json(payload), nl=False)
        return
    typer.echo(render_mermaid(ws) if fmt == "mermaid" else render_text(ws), nl=False)
    if spoke:
        em = resolve_project(ws, spoke)
        typer.echo(f"\neffective model: {spoke}")
        typer.echo(render_fields_text(em), nl=False)
        for problem in em.problems:
            typer.echo(f"problem: {problem}", err=True)


@app.command()
def lint(
    config: ConfigOpt = None,
    fmt: Annotated[str, typer.Option("--format", "-f", help="text | markdown | sarif")] = "text",
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write to file")] = None,
    sarif_base: Annotated[
        Path | None, typer.Option(help="Directory SARIF URIs are relative to (default: cwd)")
    ] = None,
) -> None:
    """Check LookML + glossary metadata. Exits 1 when any error-level finding exists."""
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


@app.command(name="compile")
def compile_cmd(
    config: ConfigOpt = None,
    check: Annotated[
        bool, typer.Option("--check", help="Fail if committed build artifacts are stale")
    ] = False,
    polish: Annotated[
        bool, typer.Option("--polish", help="Polish rule prose with an LLM (not configured)")
    ] = False,
) -> None:
    """Compile layered agent instructions, CA agent bodies and adherence tests into build/."""
    from lookml_agentops.compile.build import check_build, compile_all
    from lookml_agentops.compile.generate import CompileError

    cfg = _cfg(config)
    if polish:
        typer.echo(
            "--polish: no polisher is configured in this build. The unpolished output is the "
            "source of truth; see lookml_agentops.compile.polish for the interface.",
            err=True,
        )
        raise typer.Exit(2)
    try:
        if check:
            stale = check_build(cfg)
            if stale:
                typer.echo("stale build artifacts (run `lkagent compile`):", err=True)
                for s in stale:
                    typer.echo(f"  {s}", err=True)
                raise typer.Exit(1)
            typer.echo("build artifacts are up to date")
            return
        result = compile_all(cfg)
    except CompileError as exc:
        typer.echo(f"compile error:\n{exc}", err=True)
        raise typer.Exit(1) from exc
    for w in result.warnings:
        typer.echo(f"warning: {w}", err=True)
    for spoke, instr in sorted(result.agents.items()):
        n = {layer.layer_id: len(layer.rules) for layer in instr.layers}
        typer.echo(f"{spoke}: {n} content_hash={instr.content_hash[:12]}")
    typer.echo(f"wrote {len(result.files)} files to {result.out_dir}")


if __name__ == "__main__":  # pragma: no cover
    app()
