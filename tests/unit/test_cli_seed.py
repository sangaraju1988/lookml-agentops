from __future__ import annotations

import shutil
from pathlib import Path

from tests.conftest import EXAMPLE
from typer.testing import CliRunner

from lookml_agentops.cli import app


def test_cli_seed_prints_hash(tmp_path: Path) -> None:
    shutil.copy(EXAMPLE / "lkagent.yaml", tmp_path / "lkagent.yaml")
    res = CliRunner().invoke(app, ["seed", "-c", str(tmp_path), "--scale", "0.05", "--no-duckdb"])
    assert res.exit_code == 0, res.output
    assert "content_hash:" in res.output
    assert (tmp_path / "seed" / "manifest.json").exists()
