"""Ground truth: suite SQL executed directly on the warehouse, independent of LookML and the agent.

Engines:

* ``duckdb``   — the local synthetic seed (demo, CI, the mock agent);
* ``bigquery`` — your warehouse (``pip install 'lookml-agentops[bigquery]'``). The billing project
  comes from the env var named by ``diagnose.ground_truth.project_env``; credentials come from
  Application Default Credentials (``gcloud auth application-default login`` or
  ``GOOGLE_APPLICATION_CREDENTIALS``). Nothing is stored by lkagent.

SQL placeholders: ``$as_of`` becomes ``DATE 'YYYY-MM-DD'`` (valid in DuckDB and BigQuery), and each
``diagnose.ground_truth.params`` entry ``name: value`` replaces ``$name`` (e.g. a dataset name).

Ground truth must be aggregated. A result with more than ``max_rows`` rows is an error, so
row-level data never flows into lkagent. Only a fingerprint of each result is stored.
"""

from __future__ import annotations

import datetime as dt
import importlib
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import duckdb

from lookml_agentops.config import GroundTruthConfig


class GroundTruthError(Exception):
    pass


def normalize_value(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dt.datetime | dt.date):
        return v.isoformat()
    return v


class GroundTruthEngine(Protocol):
    name: str

    def query(self, sql: str, max_rows: int) -> list[list[Any]]: ...

    def close(self) -> None: ...


class DuckDBEngine:
    name = "duckdb"

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.con = con

    def query(self, sql: str, max_rows: int) -> list[list[Any]]:
        rows = self.con.execute(sql).fetchmany(max_rows + 1)
        return [[normalize_value(v) for v in r] for r in rows]

    def close(self) -> None:  # the connection is owned by the caller
        return None


def _bigquery() -> Any:
    """The google-cloud-bigquery module (optional [bigquery] extra), typed loosely."""
    try:
        return importlib.import_module("google.cloud.bigquery")
    except ImportError as exc:  # pragma: no cover - optional extra
        raise GroundTruthError(
            "install the [bigquery] extra: pip install 'lookml-agentops[bigquery]'"
        ) from exc


class BigQueryEngine:
    name = "bigquery"

    def __init__(self, client: Any, conf: GroundTruthConfig) -> None:
        self.client = client
        self.conf = conf

    @classmethod
    def from_config(cls, conf: GroundTruthConfig) -> BigQueryEngine:
        project = os.environ.get(conf.project_env)
        if not project:
            raise GroundTruthError(
                f"BigQuery ground truth needs environment variable {conf.project_env}"
            )
        bigquery = _bigquery()
        return cls(bigquery.Client(project=project, location=conf.location), conf)

    def _job_config(self) -> Any:
        cfg = _bigquery().QueryJobConfig(labels={"tool": "lkagent", "purpose": "ground-truth"})
        if self.conf.maximum_bytes_billed is not None:
            cfg.maximum_bytes_billed = self.conf.maximum_bytes_billed
        if self.conf.default_dataset:
            cfg.default_dataset = self.conf.default_dataset
        return cfg

    def query(self, sql: str, max_rows: int) -> list[list[Any]]:
        try:
            job = self.client.query(sql, job_config=self._job_config())
            rows = list(job.result(max_results=max_rows + 1))
        except GroundTruthError:
            raise
        except Exception as exc:  # report the failure type and message, never credentials
            raise GroundTruthError(
                f"BigQuery ground-truth query failed: {type(exc).__name__}: {exc}"
            ) from exc
        return [[normalize_value(v) for v in r.values()] for r in rows]

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()


def render_sql(text: str, as_of: dt.date, params: dict[str, str]) -> str:
    """Replace ``$as_of`` and ``$<param>`` placeholders (longest names first)."""
    subs = {"as_of": f"DATE '{as_of.isoformat()}'", **params}
    for name in sorted(subs, key=len, reverse=True):
        # escape backslashes so the value is inserted literally, not as a regex template
        text = re.sub(rf"\${re.escape(name)}\b", subs[name].replace("\\", "\\\\"), text)
    return text


class GroundTruth:
    def __init__(
        self,
        engine: GroundTruthEngine,
        as_of: dt.date,
        *,
        max_rows: int = 10_000,
        params: dict[str, str] | None = None,
    ) -> None:
        self.engine = engine
        self.as_of = as_of
        self.max_rows = max_rows
        self.params = dict(params or {})
        self._cache: dict[Path, list[list[Any]]] = {}

    def rows(self, sql_path: Path) -> list[list[Any]]:
        if sql_path not in self._cache:
            sql = render_sql(sql_path.read_text(encoding="utf-8"), self.as_of, self.params)
            rows = self.engine.query(sql, self.max_rows)
            if len(rows) > self.max_rows:
                raise GroundTruthError(
                    f"{sql_path.name} returned more than {self.max_rows} rows; ground truth must be an "
                    "aggregated result (raise diagnose.ground_truth.max_rows if this is intended)"
                )
            self._cache[sql_path] = rows
        return self._cache[sql_path]
