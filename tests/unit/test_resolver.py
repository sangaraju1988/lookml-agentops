from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import EXAMPLE
from tests.lookml_helpers import mini_config, write_projects

from lookml_agentops.config import load_config
from lookml_agentops.lookml.graph import render_mermaid, render_text
from lookml_agentops.lookml.resolve import ResolveError, load_workspace, resolve_project


@pytest.fixture(scope="module")
def example_ws():  # type: ignore[no-untyped-def]
    return load_workspace(load_config(EXAMPLE))


def test_refined_imported_field_has_importer_provenance(example_ws) -> None:  # type: ignore[no-untyped-def]
    em = resolve_project(example_ws, "finance_project")
    f = em.field("orders", "gross_revenue")
    assert f is not None
    assert [p.op for p in f.provenance] == ["define", "refine"]
    assert f.defined_at.project == "core_project"
    assert f.defined_at.file == "views/orders.view.lkml"
    assert f.last_at.project == "finance_project"
    assert f.last_at.file == "views/finance_refinements.view.lkml"
    assert f.last_at.line == 5
    # the refinement changed wording but not SQL
    assert f.sql == "${gross_amount}"
    assert f.params["group_label"] == "Finance KPIs"
    assert f.origin_project == "core_project"


def test_refinements_do_not_leak_into_other_importers(example_ws) -> None:  # type: ignore[no-untyped-def]
    logistics = resolve_project(example_ws, "logistics_project")
    assert logistics.field("orders", "refund_rate") is None
    gr = logistics.field("orders", "gross_revenue")
    assert gr is not None and [p.op for p in gr.provenance] == ["define"]
    assert logistics.field("shipments", "avg_transit_days") is not None


def test_extended_explore_inherits_guard_and_joins(example_ws) -> None:  # type: ignore[no-untyped-def]
    em = resolve_project(example_ws, "finance_project")
    e = em.explores["finance_invoices"]
    assert not e.extension_required
    assert e.sql_always_where and "is_test_account" in e.sql_always_where
    assert list(e.joins)[-1] == "payments"
    assert "invoice_fiscal" in e.joins
    assert e.always_filter == {"invoices.is_void": "no"}
    assert [p.loc.project for p in e.provenance] == ["core_project", "finance_project"]
    assert [x.name for x in em.queryable_explores()] == ["finance_invoices", "finance_orders"]


def test_example_resolves_without_problems(example_ws) -> None:  # type: ignore[no-untyped-def]
    for p in ("core_project", "finance_project", "logistics_project", "procurement_project"):
        assert resolve_project(example_ws, p).problems == []


def test_import_graph_renders(example_ws) -> None:  # type: ignore[no-untyped-def]
    assert render_text(example_ws) == (
        "finance_project\n└── core_project (local_dependency)\n"
        "logistics_project\n└── core_project (local_dependency)\n"
        "procurement_project (standalone: imports nothing)\n"
    )
    mm = render_mermaid(example_ws)
    assert mm.startswith("graph LR\n")
    assert "finance_project -->|local_dependency| core_project" in mm


def test_exemption_comment_attaches_to_next_field(example_ws) -> None:  # type: ignore[no-untyped-def]
    em = resolve_project(example_ws, "logistics_project")
    shipments = em.views["shipments"]
    assert shipments.fields["delivered"].exemptions == {"LKA010"}
    assert shipments.fields["shipped"].exemptions == set()


BASE = {
    "base/manifest.lkml": 'project_name: "base"\n',
    "base/base.model.lkml": 'connection: "c"\ninclude: "/views/*.view"\n',
    "base/views/a.view.lkml": (
        "view: a {\n  sql_table_name: t.a ;;\n  dimension: x {\n    sql: ${TABLE}.x ;;\n"
        '    label: "X"\n  }\n  measure: m {\n    type: sum\n    sql: ${x} ;;\n  }\n}\n'
        "explore: a_base {\n  extension: required\n  view_name: a\n"
        "  sql_always_where: 1=1 ;;\n}\n"
    ),
}


def test_refinements_apply_in_include_order_and_extends_first(tmp_path: Path) -> None:
    write_projects(
        tmp_path,
        {
            **BASE,
            "sp/manifest.lkml": 'project_name: "sp"\nlocal_dependency: { project: "base" }\n',
            "sp/sp.model.lkml": (
                'connection: "c"\ninclude: "//base/views/*.view.lkml"\n'
                'include: "/r1.view.lkml"\ninclude: "/r2.view.lkml"\ninclude: "e.explore"\n'
            ),
            "sp/r1.view.lkml": 'view: +a {\n  dimension: x {\n    label: "first"\n  }\n}\n',
            "sp/r2.view.lkml": (
                'view: +a {\n  dimension: x {\n    label: "second"\n  }\n'
                '  dimension: y {\n    label: "Y"\n    sql: 1 ;;\n  }\n}\n'
            ),
            "sp/e.explore.lkml": (
                'explore: a_ext {\n  extends: [a_base]\n  view_name: a\n  label: "child"\n}\n'
                'explore: +a_ext {\n  label: "refined child"\n}\n'
            ),
        },
    )
    ws = load_workspace(mini_config(tmp_path, ["base", "sp"]))
    em = resolve_project(ws, "sp")
    assert em.problems == []
    x = em.field("a", "x")
    assert x is not None and x.label == "second"
    assert [str(p.loc) for p in x.provenance] == [
        "base/views/a.view.lkml:3",
        "sp/r1.view.lkml:2",
        "sp/r2.view.lkml:2",
    ]
    y = em.field("a", "y")
    assert y is not None and y.origin_project == "sp"
    e = em.explores["a_ext"]
    assert e.label == "refined child"
    assert e.sql_always_where == "1=1"
    assert [f"{p}/{f}" for p, f in em.included] == [
        "sp/sp.model.lkml",
        "base/views/a.view.lkml",
        "sp/r1.view.lkml",
        "sp/r2.view.lkml",
        "sp/e.explore.lkml",
    ]


def test_undeclared_import_is_a_problem(tmp_path: Path) -> None:
    write_projects(
        tmp_path,
        {
            **BASE,
            "sp/manifest.lkml": 'project_name: "sp"\n',
            "sp/sp.model.lkml": 'connection: "c"\ninclude: "//base/views/*.view.lkml"\n',
        },
    )
    em = resolve_project(load_workspace(mini_config(tmp_path, ["base", "sp"])), "sp")
    assert any("not declared in manifest" in p for p in em.problems)


def test_unknown_refinement_and_missing_include(tmp_path: Path) -> None:
    write_projects(
        tmp_path,
        {
            "sp/sp.model.lkml": 'connection: "c"\ninclude: "/nope/*.view"\ninclude: "/r.view"\n',
            "sp/r.view.lkml": "view: +ghost {\n  dimension: z {}\n}\n",
        },
    )
    em = resolve_project(load_workspace(mini_config(tmp_path, ["sp"])), "sp")
    assert any("matched no files" in p for p in em.problems)
    assert any("unknown view 'ghost'" in p for p in em.problems)


def test_extends_cycle_raises(tmp_path: Path) -> None:
    write_projects(
        tmp_path,
        {
            "sp/sp.model.lkml": (
                'connection: "c"\nexplore: a {\n  extends: [b]\n}\nexplore: b {\n  extends: [a]\n}\n'
            ),
        },
    )
    with pytest.raises(ResolveError, match="cycle"):
        resolve_project(load_workspace(mini_config(tmp_path, ["sp"])), "sp")


def test_view_extends_marks_inherited_fields(tmp_path: Path) -> None:
    write_projects(
        tmp_path,
        {
            "sp/sp.model.lkml": 'connection: "c"\ninclude: "/v.view"\n',
            "sp/v.view.lkml": (
                "view: base {\n  extension: required\n  dimension: d {\n    sql: 1 ;;\n  }\n}\n"
                'view: child {\n  extends: [base]\n  dimension: d {\n    label: "D"\n  }\n}\n'
            ),
        },
    )
    em = resolve_project(load_workspace(mini_config(tmp_path, ["sp"])), "sp")
    d = em.field("child", "d")
    assert d is not None and d.sql == "1" and d.label == "D"
    assert [p.op for p in d.provenance] == ["define", "extend", "override"]
    assert not em.views["child"].extension_required


def test_import_cycle_is_a_clear_error(tmp_path: Path) -> None:
    write_projects(
        tmp_path,
        {
            "a/manifest.lkml": 'project_name: "a"\nlocal_dependency: { project: "b" }\n',
            "a/a.model.lkml": 'connection: "c"\n',
            "b/manifest.lkml": 'project_name: "b"\nlocal_dependency: { project: "c" }\n',
            "b/b.model.lkml": 'connection: "c"\n',
            "c/manifest.lkml": 'project_name: "c"\nlocal_dependency: { project: "a" }\n',
            "c/c.model.lkml": 'connection: "c"\n',
        },
    )
    with pytest.raises(
        ResolveError, match=r"import cycle between LookML projects: a -> b -> c -> a"
    ):
        load_workspace(mini_config(tmp_path, ["a", "b", "c"]))


def test_diamond_import_dag_resolves(tmp_path: Path) -> None:
    view = "view: {n} {{\n  sql_table_name: t.{n} ;;\n  dimension: x {{\n    sql: 1 ;;\n  }}\n}}\n"
    write_projects(
        tmp_path,
        {
            "base/manifest.lkml": 'project_name: "base"\n',
            "base/base.model.lkml": 'connection: "c"\ninclude: "/v.view"\n',
            "base/v.view.lkml": view.format(n="shared"),
            "left/manifest.lkml": 'project_name: "left"\nlocal_dependency: { project: "base" }\n',
            "left/left.model.lkml": 'connection: "c"\ninclude: "//base/v.view"\n',
            "right/manifest.lkml": 'project_name: "right"\nlocal_dependency: { project: "base" }\n',
            "right/right.model.lkml": 'connection: "c"\ninclude: "//base/v.view"\n',
            "top/manifest.lkml": (
                'project_name: "top"\nlocal_dependency: { project: "left" }\n'
                'local_dependency: { project: "right" }\nlocal_dependency: { project: "base" }\n'
            ),
            "top/top.model.lkml": 'connection: "c"\ninclude: "//base/v.view"\n',
        },
    )
    ws = load_workspace(mini_config(tmp_path, ["base", "left", "right", "top"]))
    em = resolve_project(ws, "top")
    assert em.imports == ["base", "left", "right"]
    assert em.field("shared", "x") is not None and em.problems == []
