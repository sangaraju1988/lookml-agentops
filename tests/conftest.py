from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "harborline"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
AS_OF = dt.date(2026, 1, 20)
TEST_SCALE = 0.1


@pytest.fixture(scope="session")
def small_seed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small, full-fidelity seed (all traps present) loaded into DuckDB; returns db path."""
    from lookml_agentops.seed.run import run_seed

    root = tmp_path_factory.mktemp("seed")
    db = root / "harborline.duckdb"
    run_seed(
        seed=20260120,
        as_of=AS_OF,
        scale=TEST_SCALE,
        csv_dir=root / "data",
        duckdb_path=db,
        manifest_path=None,
    )
    return db
