"""Execute ground-truth SQL against the local DuckDB seed (independent of LookML)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb


def normalize_value(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dt.datetime | dt.date):
        return v.isoformat()
    return v


class GroundTruth:
    def __init__(self, con: duckdb.DuckDBPyConnection, as_of: dt.date) -> None:
        self.con = con
        self.as_of = as_of
        self._cache: dict[Path, list[list[Any]]] = {}

    def rows(self, sql_path: Path) -> list[list[Any]]:
        if sql_path not in self._cache:
            sql = sql_path.read_text(encoding="utf-8").replace(
                "$as_of", f"DATE '{self.as_of.isoformat()}'"
            )
            self._cache[sql_path] = [
                [normalize_value(v) for v in r] for r in self.con.execute(sql).fetchall()
            ]
        return self._cache[sql_path]
