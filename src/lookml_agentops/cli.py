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


if __name__ == "__main__":  # pragma: no cover
    app()
