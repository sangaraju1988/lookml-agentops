"""``lkagent seed`` implementation."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from pydantic import BaseModel

from lookml_agentops._util.hashing import sha256_bytes, sha256_obj
from lookml_agentops._util.io import write_json
from lookml_agentops.seed.generator import generate
from lookml_agentops.seed.loaders.duckdb_loader import load_duckdb
from lookml_agentops.seed.writer import write_csvs


class TableStat(BaseModel):
    rows: int
    sha256: str


class SeedManifest(BaseModel):
    seed: int
    scale: float
    as_of: dt.date
    tables: dict[str, TableStat]
    content_hash: str


def run_seed(
    *,
    seed: int,
    as_of: dt.date,
    scale: float,
    csv_dir: Path,
    duckdb_path: Path | None,
    manifest_path: Path | None,
) -> SeedManifest:
    result = generate(seed, as_of, scale)
    rendered = write_csvs(result.tables, csv_dir)
    stats = {
        name: TableStat(rows=rows, sha256=sha256_bytes(data))
        for name, (rows, data) in rendered.items()
    }
    content_hash = sha256_obj({k: v.sha256 for k, v in sorted(stats.items())})
    manifest = SeedManifest(
        seed=seed, scale=scale, as_of=as_of, tables=stats, content_hash=content_hash
    )
    if duckdb_path is not None:
        load_duckdb(csv_dir, duckdb_path)
    if manifest_path is not None:
        write_json(manifest_path, manifest.model_dump(mode="json"))
    return manifest
