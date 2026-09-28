from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pytest
from tests.conftest import EXAMPLE
from tests.lookml_helpers import mini_config, write_projects

from lookml_agentops.config import load_config
from lookml_agentops.lookml.resolve import load_workspace, resolve_project
from lookml_agentops.lookml.sqlgen import QueryFilter, SqlGenError, build_query


@pytest.fixture(scope="module")
def fin():  # type: ignore[no-untyped-def]
    cfg = load_config(EXAMPLE)
    return resolve_project(load_workspace(cfg), "finance_project")


def test_guard_and_joins_are_applied(fin) -> None:  # type: ignore[no-untyped-def]
    sql, cols = build_query(
        fin, "finance_orders", ["regions.region_name", "orders.net_revenue"], []
    )
    assert cols == ["regions.region_name", "orders.net_revenue"]
    assert "NOT (customers.is_test_account)" in sql
    assert "LEFT JOIN raw.regions AS regions" in sql
    assert "order_refund_facts" in sql  # pulled in by the net_revenue SQL
    assert "created_fiscal" not in sql  # unused joins are not rendered


def test_time_window_and_timeframes(fin, small_seed: Path) -> None:  # type: ignore[no-untyped-def]
    flt = QueryFilter("orders.created_date", start=dt.date(2025, 8, 1), end=dt.date(2025, 11, 1))
    sql, _ = build_query(
        fin, "finance_orders", ["orders.created_month", "orders.order_count"], [flt]
    )
    con = duckdb.connect(str(small_seed), read_only=True)
    rows = con.execute(sql).fetchall()
    assert [r[0] for r in rows] == ["2025-08", "2025-09", "2025-10"]


def test_yesno_filter_and_filtered_measure(fin) -> None:  # type: ignore[no-untyped-def]
    sql, _ = build_query(
        fin,
        "finance_invoices",
        ["invoices.recognized_revenue"],
        [QueryFilter("invoices.is_void", ["no"])],
    )
    assert "NOT COALESCE(invoices.status = 'void', FALSE)" in sql
    assert "SUM(CASE WHEN" in sql


def test_fanout_is_refused(tmp_path: Path) -> None:
    write_projects(
        tmp_path,
        {
            "p/p.model.lkml": (
                'connection: "c"\nview: a {\n  sql_table_name: t.a ;;\n  dimension: id {\n    primary_key: yes\n  }\n}\n'
                "view: b {\n  sql_table_name: t.b ;;\n  measure: total {\n    type: sum\n    sql: ${TABLE}.x ;;\n  }\n}\n"
                "explore: a {\n  join: b {\n    relationship: one_to_many\n    sql_on: ${a.id} = ${b.a_id} ;;\n  }\n}\n"
            ),
        },
    )
    em = resolve_project(load_workspace(mini_config(tmp_path, ["p"])), "p")
    with pytest.raises(SqlGenError, match="symmetric aggregates"):
        build_query(em, "a", ["b.total"], [])


def test_unknown_field_raises(fin) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SqlGenError, match="unknown field"):
        build_query(fin, "finance_orders", ["orders.nope"], [])
