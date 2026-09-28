from __future__ import annotations

from pathlib import Path

import duckdb
from tests.conftest import AS_OF, TEST_SCALE

from lookml_agentops.seed.run import run_seed


def _run(tmp: Path) -> str:
    m = run_seed(
        seed=20260120,
        as_of=AS_OF,
        scale=TEST_SCALE,
        csv_dir=tmp / "data",
        duckdb_path=None,
        manifest_path=tmp / "manifest.json",
    )
    return m.content_hash


def test_seed_is_byte_deterministic(tmp_path: Path) -> None:
    h1 = _run(tmp_path / "a")
    h2 = _run(tmp_path / "b")
    assert h1 == h2
    for f in sorted((tmp_path / "a" / "data").iterdir()):
        assert f.read_bytes() == (tmp_path / "b" / "data" / f.name).read_bytes(), f.name
    assert (tmp_path / "a" / "manifest.json").read_bytes() == (
        tmp_path / "b" / "manifest.json"
    ).read_bytes()


def test_different_seed_changes_hash(tmp_path: Path) -> None:
    a = run_seed(
        seed=1,
        as_of=AS_OF,
        scale=TEST_SCALE,
        csv_dir=tmp_path / "a",
        duckdb_path=None,
        manifest_path=None,
    )
    b = run_seed(
        seed=2,
        as_of=AS_OF,
        scale=TEST_SCALE,
        csv_dir=tmp_path / "b",
        duckdb_path=None,
        manifest_path=None,
    )
    assert a.content_hash != b.content_hash


def _one(db: Path, sql: str) -> object:
    con = duckdb.connect(str(db), read_only=True)
    try:
        row = con.execute(sql).fetchone()
        assert row is not None
        return row[0]
    finally:
        con.close()


def test_trap_dirty_status_values(small_seed: Path) -> None:
    vals = {r for r in _rows(small_seed, "SELECT DISTINCT status FROM raw.shipments")}
    assert {"Shipped", "shipped", "SHIPPED ", "Shipped - Partial"} <= vals


def test_trap_near_duplicate_names(small_seed: Path) -> None:
    carriers = set(_rows(small_seed, "SELECT carrier_name FROM raw.carriers"))
    assert {"Northstar Freight", "North Star Freight LLC"} <= carriers
    regions = set(_rows(small_seed, "SELECT region_name FROM raw.regions"))
    assert {"Pacific NW", "Pacific Northwest"} <= regions
    assert _one(small_seed, "SELECT count(*) FROM raw.customers WHERE region_id = 6") > 0


def test_trap_test_accounts_and_soft_deletes(small_seed: Path) -> None:
    assert (
        _one(
            small_seed,
            """SELECT count(*) FROM raw.orders o JOIN raw.customers c
        USING (customer_id) WHERE c.is_test_account""",
        )
        > 0
    )
    assert _one(small_seed, "SELECT count(*) FROM raw.orders WHERE is_deleted") > 0


def test_trap_late_refunds_cross_quarters(small_seed: Path) -> None:
    n = _one(
        small_seed,
        """
        SELECT count(*) FROM raw.refunds r JOIN raw.orders o USING (order_id)
        JOIN raw.fiscal_calendar a ON a.calendar_date = CAST(o.order_ts_utc AS DATE)
        JOIN raw.fiscal_calendar b ON b.calendar_date = CAST(r.refunded_at_utc AS DATE)
        WHERE a.fiscal_quarter_label <> b.fiscal_quarter_label""",
    )
    assert n > 0


def test_trap_local_vs_utc_dates_differ(small_seed: Path) -> None:
    n = _one(
        small_seed,
        """
        SELECT count(*) FROM raw.shipments s JOIN raw.warehouses w USING (warehouse_id)
        WHERE CAST(s.shipped_at_local AS DATE)
           <> CAST(timezone('UTC', timezone(w.timezone, s.shipped_at_local)) AS DATE)""",
    )
    assert n > 0


def test_revenue_measures_differ(small_seed: Path) -> None:
    gross = _one(small_seed, "SELECT sum(gross_amount) FROM raw.orders")
    net = _one(small_seed, "SELECT sum(gross_amount - discount_amount) FROM raw.orders")
    recognized = _one(small_seed, "SELECT sum(amount) FROM raw.invoices WHERE status <> 'void'")
    assert len({gross, net, recognized}) == 3


def test_no_real_looking_emails(small_seed: Path) -> None:
    bad = _one(
        small_seed,
        """SELECT count(*) FROM raw.customers
        WHERE NOT regexp_matches(email, '@example\\.(com|net|org)$')""",
    )
    assert bad == 0


def _rows(db: Path, sql: str) -> list[object]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return [r[0] for r in con.execute(sql).fetchall()]
    finally:
        con.close()
