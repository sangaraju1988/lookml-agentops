"""Load seed CSVs into a local DuckDB file under schema ``raw``."""

from __future__ import annotations

from pathlib import Path

import duckdb

from lookml_agentops.seed.schema import RAW_SCHEMA, TABLES


def load_duckdb(csv_dir: Path, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    try:
        con.execute(f"CREATE SCHEMA {RAW_SCHEMA}")
        for name in sorted(TABLES):
            cols = TABLES[name]
            ddl = ", ".join(f"{c} {t}" for c, t in cols)
            con.execute(f"CREATE TABLE {RAW_SCHEMA}.{name} ({ddl})")
            csv_path = (csv_dir / f"{name}.csv").as_posix()
            col_types = "{" + ", ".join(f"'{c}': '{t}'" for c, t in cols) + "}"
            con.execute(
                f"INSERT INTO {RAW_SCHEMA}.{name} SELECT * FROM read_csv("
                f"'{csv_path}', header=true, columns={col_types}, nullstr='', quote='\"')"
            )
    finally:
        con.close()
