"""Ground-truth engines, tested offline (the BigQuery client is faked)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest

from lookml_agentops.config import GroundTruthConfig, load_config
from lookml_agentops.diagnose.ground_truth import (
    BigQueryEngine,
    DuckDBEngine,
    GroundTruth,
    GroundTruthError,
    render_sql,
)
from lookml_agentops.diagnose.run import RunOptions, run_diagnose

AS_OF = dt.date(2026, 1, 20)


class _Row:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def values(self) -> list[Any]:
        return self._values


class _Job:
    def __init__(self, rows: list[list[Any]]) -> None:
        self.rows = rows
        self.max_results: int | None = None

    def result(self, max_results: int | None = None) -> list[_Row]:
        self.max_results = max_results
        return [_Row(r) for r in self.rows[: max_results or None]]


class FakeBigQuery:
    """Records queries; answers from a DuckDB connection or a fixed row list."""

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection | None = None,
        rows: list[list[Any]] | None = None,
        fail: Exception | None = None,
    ) -> None:
        self.con, self.fixed, self.fail = con, rows, fail
        self.queries: list[tuple[str, Any]] = []
        self.closed = False

    def query(self, sql: str, job_config: Any = None) -> _Job:
        self.queries.append((sql, job_config))
        if self.fail:
            raise self.fail
        if self.fixed is not None:
            return _Job(self.fixed)
        assert self.con is not None
        return _Job([list(r) for r in self.con.execute(sql).fetchall()])

    def close(self) -> None:
        self.closed = True


def test_render_sql_placeholders() -> None:
    sql = "SELECT * FROM $dataset.orders WHERE d < $as_of AND x = '$dataset_suffix' AND p = '$path'"
    out = render_sql(sql, AS_OF, {"dataset": "acme_raw", "dataset_suffix": "s1", "path": r"a\b"})
    assert out == (
        "SELECT * FROM acme_raw.orders WHERE d < DATE '2026-01-20' AND x = 's1' "
        r"AND p = 'a\b'"
    )


def test_bigquery_engine_job_config_and_normalization() -> None:
    pytest.importorskip("google.cloud.bigquery")
    conf = GroundTruthConfig(
        engine="bigquery", default_dataset="proj.analytics", maximum_bytes_billed=123
    )
    fake = FakeBigQuery(rows=[["Midwest", Decimal("10.50"), dt.date(2025, 8, 1)]])
    engine = BigQueryEngine(fake, conf)
    assert engine.query("SELECT 1", max_rows=5) == [["Midwest", 10.5, "2025-08-01"]]
    _, job_config = fake.queries[0]
    assert job_config.maximum_bytes_billed == 123
    assert str(job_config.default_dataset) == "proj.analytics"
    assert job_config.labels == {"tool": "lkagent", "purpose": "ground-truth"}
    engine.close()
    assert fake.closed


def test_row_limit_keeps_ground_truth_aggregated(tmp_path: Path) -> None:
    pytest.importorskip("google.cloud.bigquery")
    sql = tmp_path / "gt.sql"
    sql.write_text("SELECT * FROM big_table")
    gt = GroundTruth(
        BigQueryEngine(FakeBigQuery(rows=[[i] for i in range(50)]), GroundTruthConfig()),
        AS_OF,
        max_rows=10,
    )
    with pytest.raises(GroundTruthError, match="more than 10 rows"):
        gt.rows(sql)
    rows_sql = tmp_path / "r.sql"
    rows_sql.write_text("SELECT * FROM range(10)")
    with pytest.raises(GroundTruthError, match="aggregated"):
        GroundTruth(DuckDBEngine(duckdb.connect()), AS_OF, max_rows=3).rows(rows_sql)


def test_bigquery_errors_are_wrapped_and_env_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("google.cloud.bigquery")
    engine = BigQueryEngine(
        FakeBigQuery(fail=RuntimeError("Access Denied: table")), GroundTruthConfig()
    )
    with pytest.raises(GroundTruthError, match="RuntimeError: Access Denied"):
        engine.query("SELECT 1", max_rows=5)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    with pytest.raises(GroundTruthError, match="GOOGLE_CLOUD_PROJECT"):
        BigQueryEngine.from_config(GroundTruthConfig(engine="bigquery"))


def test_diagnose_run_with_bigquery_ground_truth(harbor: Path, small_seed: Path) -> None:
    pytest.importorskip("google.cloud.bigquery")
    cfg = load_config(harbor)
    cfg.diagnose.ground_truth.engine = "bigquery"
    fake = FakeBigQuery(con=duckdb.connect(str(small_seed), read_only=True))
    rec = run_diagnose(
        cfg,
        RunOptions(agents=["procurement-buyer"]),
        gt_engine=BigQueryEngine(fake, cfg.diagnose.ground_truth),
    )
    assert [r.test_id for r in rec.results if r.status != "pass"] == []
    assert {r.test_id for r in rec.results if r.gt_hash} == {"proc-001", "proc-002", "proc-003"}
    assert all(
        "DATE '2026-01-20'" in q for q, _ in fake.queries
    )  # $as_of rendered for the warehouse
    assert rec.inputs["data:warehouse"].meta["engine"] == "bigquery"
    assert rec.inputs["data:warehouse"].meta["seed_manifest"] is None
