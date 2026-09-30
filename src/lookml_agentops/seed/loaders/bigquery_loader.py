"""Optional: load the seed CSVs into BigQuery (``pip install lookml-agentops[bigquery]``).

Credentials come from Application Default Credentials / ``GOOGLE_APPLICATION_CREDENTIALS``;
nothing is read from or written to config files.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from lookml_agentops.seed.schema import TABLES

_BQ_TYPES = {
    "INTEGER": "INT64",
    "VARCHAR": "STRING",
    "DATE": "DATE",
    "TIMESTAMP": "DATETIME",  # naive wall-clock values, same as DuckDB
    "BOOLEAN": "BOOL",
}


def _bq_type(duck_type: str) -> str:
    if duck_type.startswith("DECIMAL"):
        return "NUMERIC"
    return _BQ_TYPES[duck_type]


def load_bigquery(csv_dir: Path, project: str, dataset: str, location: str = "US") -> None:
    try:
        bigquery: Any = importlib.import_module("google.cloud.bigquery")
    except ImportError as exc:  # pragma: no cover - optional extra
        raise RuntimeError(
            "install the [bigquery] extra: pip install lookml-agentops[bigquery]"
        ) from exc

    client: Any = bigquery.Client(project=project)
    ds = bigquery.Dataset(f"{project}.{dataset}")
    ds.location = location
    client.create_dataset(ds, exists_ok=True)
    for name in sorted(TABLES):
        schema = [bigquery.SchemaField(c, _bq_type(t)) for c, t in TABLES[name]]
        job_config = bigquery.LoadJobConfig(
            schema=schema,
            source_format=bigquery.SourceFormat.CSV,
            skip_leading_rows=1,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
        with (csv_dir / f"{name}.csv").open("rb") as fh:
            job = client.load_table_from_file(
                fh, f"{project}.{dataset}.{name}", job_config=job_config
            )
        job.result()
