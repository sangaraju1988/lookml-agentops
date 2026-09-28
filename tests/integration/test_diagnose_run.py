from __future__ import annotations

from pathlib import Path

import duckdb

from lookml_agentops.config import load_config
from lookml_agentops.diagnose.history import History
from lookml_agentops.diagnose.modeldiff import diff_elements
from lookml_agentops.diagnose.run import RunOptions, explain_deps, run_diagnose


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new, 1))


def test_baseline_passes_and_records_fingerprints(harbor: Path) -> None:
    cfg = load_config(harbor)
    rec = run_diagnose(cfg, RunOptions())
    assert [(r.test_id, r.details) for r in rec.results if r.status != "pass"] == []
    assert {r.agent for r in rec.results} == {
        "finance-analyst",
        "logistics-ops",
        "procurement-buyer",
    }
    kinds = {i.kind for i in rec.inputs.values()}
    assert kinds == {
        "lookml_project",
        "agent_spec",
        "catalog",
        "test_suite",
        "runner",
        "data",
        "config",
    }
    assert rec.inputs["agent:company-baseline"].owner == "central-data-platform"
    assert rec.inputs["runner:mock"].owner == "bi-platform"
    with History(cfg.path(cfg.diagnose.history)) as h:
        loaded = h.load(rec.info.run_id)
        assert set(h.baselines()) == {r.test_id for r in rec.results if r.gt_hash}
    assert loaded.elements.keys() == rec.elements.keys()
    r = loaded.result("fin-003")
    assert r is not None and {d["element_id"] for d in r.deps} >= {
        "field:finance_project/orders.net_revenue",
        "rule:fiscal-quarter",
        "gt:fin-003",
        "test:fin-003",
    }
    # no row-level data is stored
    assert all("rows" not in x.answer for x in loaded.results)


def test_explain_deps_prints_provenance(harbor: Path) -> None:
    cfg = load_config(harbor)
    rec = run_diagnose(cfg, RunOptions(agents=["finance-analyst"], record=False))
    text = explain_deps(rec, "fin-003", cfg)
    assert "field:finance_project/orders.net_revenue" in text
    assert "core_project/views/orders.view.lkml:87  owner=central-data-platform" in text
    assert "rule:fiscal-quarter" in text and "agents/shared/company-baseline.agent.md:10" in text
    assert "via       field:finance_project/orders.gross_amount" in text


def test_field_level_diff_points_at_the_defining_project(harbor: Path) -> None:
    cfg = load_config(harbor)
    a = run_diagnose(cfg, RunOptions(agents=["finance-analyst"], record=False))
    _edit(
        harbor / "projects/core_project/views/orders.view.lkml",
        "sql: ${gross_amount} - ${discount_amount} - COALESCE(${order_refund_facts.refund_amount}, 0) ;;",
        "sql: ${gross_amount} - ${discount_amount} ;;",
    )
    b = run_diagnose(load_config(harbor), RunOptions(agents=["finance-analyst"], record=False))
    changes = [
        c for c in diff_elements(a.elements, b.elements) if c.element_id.startswith("field:")
    ]
    assert [c.element_id for c in changes] == ["field:finance_project/orders.net_revenue"]
    ch = changes[0]
    assert ch.facet == "semantic" and ch.input_id == "lookml:core_project"
    assert ch.location == "core_project/views/orders.view.lkml:87"
    assert [p.param for p in ch.params] == ["sql"]


def test_mock_consumes_the_compiled_spec(harbor: Path) -> None:
    cfg = load_config(harbor)
    before = run_diagnose(cfg, RunOptions(agents=["finance-analyst"], record=False))
    _edit(
        harbor / "agents/finance-analyst.agent.md",
        '"Sales" and "net sales" mean net revenue.',
        '"Sales" and "net sales" mean gross revenue.',
    )
    after = run_diagnose(load_config(harbor), RunOptions(agents=["finance-analyst"], record=False))
    b = before.result("adh.finance-analyst.rule.sales-means-net.1")
    a = after.result("adh.finance-analyst.rule.sales-means-net.1")
    assert b is not None and a is not None
    assert "orders.net_revenue" in b.answer["fields"]
    assert "orders.gross_revenue" in a.answer["fields"]


def test_v1_history_is_moved_aside(tmp_path: Path) -> None:
    path = tmp_path / "history.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE runs (run_id VARCHAR, seq INTEGER)")
    con.close()
    h = History(path)
    assert h.migration_note and "v1" in h.migration_note
    assert h.run_ids() == []
    h.close()
    assert (tmp_path / "history.v1.bak.duckdb").exists()
