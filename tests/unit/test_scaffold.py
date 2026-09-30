from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import pytest
from tests.conftest import EXAMPLE
from tests.lookml_helpers import write_projects
from typer.testing import CliRunner

from lookml_agentops.cli import app
from lookml_agentops.config import load_config
from lookml_agentops.generate.compile import bind_all, compile_agents
from lookml_agentops.lookml.resolve import ResolveError
from lookml_agentops.scaffold import ScaffoldError, agent_id_for, discover_projects, scaffold

AS_OF = dt.date(2026, 1, 20)


@pytest.fixture
def lookml(tmp_path: Path) -> Path:
    dst = tmp_path / "repos"
    shutil.copytree(EXAMPLE / "projects", dst)
    return dst


def test_discover_uses_manifest_project_names(lookml: Path) -> None:
    (lookml / "core_project").rename(lookml / "shared-models")  # dir name differs from project_name
    found = discover_projects(lookml)
    assert sorted(found) == [
        "core_project",
        "finance_project",
        "logistics_project",
        "procurement_project",
    ]
    assert found["core_project"].name == "shared-models"
    assert agent_id_for("finance_project") == "finance-analyst"


def test_scaffold_builds_a_working_setup(lookml: Path, tmp_path: Path) -> None:
    root = tmp_path / "agentops"
    res = scaffold(root, discover_projects(lookml), as_of=AS_OF)
    assert sorted(res.agents) == ["finance-analyst", "logistics-analyst", "procurement-analyst"]
    assert res.agents["finance-analyst"] == [
        "finance_project::finance_invoices",
        "finance_project::finance_orders",
    ]
    cfg = load_config(root)
    assert cfg.projects["core_project"].path == "../repos/core_project"
    assert sorted(cfg.suites) == sorted(res.agents)
    assert cfg.diagnose.runner == "ca" and cfg.diagnose.ground_truth.engine == "duckdb"
    # generated specs are valid: no spec findings, every agent compiles, PII is guarded
    assert [f for f in bind_all(cfg).findings if f.rule_id.startswith("LKS")] == []
    agents = compile_agents(cfg)
    assert sorted(agents) == sorted(res.agents)
    fin = agents["finance-analyst"].spec
    assert fin.guardrails and fin.guardrails[0].covers
    assert [q.golden_id for q in fin.golden_queries] == [
        "gq-finance-invoices-total",
        "gq-finance-orders-total",
    ]
    assert any(
        t.id == "adh.finance-analyst.golden.gq-finance-orders-total"
        for t in agents["finance-analyst"].tests
    )


def test_scaffold_is_deterministic_and_protects_existing_files(
    lookml: Path, tmp_path: Path
) -> None:
    root = tmp_path / "agentops"
    projects = discover_projects(lookml)
    scaffold(root, projects, as_of=AS_OF)
    first = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(ScaffoldError, match="pass --force"):
        scaffold(root, projects, as_of=AS_OF)
    scaffold(root, projects, as_of=AS_OF, force=True)
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == first


def test_libraries_and_explore_limit(tmp_path: Path) -> None:
    view = "view: v {\n  sql_table_name: t.v ;;\n  measure: n {\n    type: count\n  }\n}\n"
    explores = "".join(f"explore: e{i} {{\n  view_name: v\n}}\n" for i in range(7))
    write_projects(
        tmp_path / "src",
        {
            "lib/manifest.lkml": 'project_name: "lib"\n',
            "lib/v.view.lkml": view,
            "wide/manifest.lkml": 'project_name: "wide"\nlocal_dependency: { project: "lib" }\n',
            "wide/wide.model.lkml": 'connection: "c"\ninclude: "//lib/v.view"\n' + explores,
        },
    )
    res = scaffold(tmp_path / "out", discover_projects(tmp_path / "src"), as_of=AS_OF)
    assert res.libraries == ["lib"]
    assert len(res.agents["wide-analyst"]) == 5
    assert any("beyond the CA limit of 5" in n for n in res.notes)
    assert "not included: e5, e6" in (tmp_path / "out/agents/wide-analyst.agent.md").read_text()
    assert compile_agents(load_config(tmp_path / "out"))


def test_import_cycle_is_reported(tmp_path: Path) -> None:
    write_projects(
        tmp_path / "src",
        {
            "a/manifest.lkml": 'project_name: "a"\nlocal_dependency: { project: "b" }\n',
            "a/a.model.lkml": 'connection: "c"\n',
            "b/manifest.lkml": 'project_name: "b"\nlocal_dependency: { project: "a" }\n',
            "b/b.model.lkml": 'connection: "c"\n',
        },
    )
    with pytest.raises(ResolveError, match="import cycle"):
        scaffold(tmp_path / "out", discover_projects(tmp_path / "src"), as_of=AS_OF)


def test_cli_init(lookml: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    out = tmp_path / "agentops"
    res = runner.invoke(
        app,
        [
            "init",
            str(out),
            "--project",
            f"procurement_project={lookml / 'procurement_project'}",
            "--as-of",
            "2026-01-20",
            "--warehouse",
            "bigquery",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "agent procurement-analyst: procurement_project::purchase_orders" in res.output
    assert "compile: 1 agent(s) compile cleanly" in res.output
    assert load_config(out).diagnose.ground_truth.engine == "bigquery"
    bad = runner.invoke(app, ["init", str(tmp_path / "x"), "--project", "no-equals-sign"])
    assert bad.exit_code == 2 and "NAME=PATH" in bad.output
    none = runner.invoke(app, ["init", str(tmp_path / "y"), "--scan", str(tmp_path / "empty")])
    assert none.exit_code == 2
