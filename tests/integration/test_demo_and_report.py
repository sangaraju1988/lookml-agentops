from __future__ import annotations

import re
import shutil
from pathlib import Path

from typer.testing import CliRunner

from lookml_agentops.cli import app
from lookml_agentops.demo import run_demo


def _source(example_copy: Path, small_seed: Path) -> Path:
    dst = example_copy / "seed" / "harborline.duckdb"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(small_seed, dst)
    return example_copy


def test_demo_attributes_vendor_hub_and_spoke(
    example_copy: Path, small_seed: Path, tmp_path: Path
) -> None:
    src = _source(example_copy, small_seed)
    before = sorted(p.read_bytes() for p in (src / "lookml").rglob("*.lkml"))
    res = run_demo(src, tmp_path / "demo", log=lambda _: None)
    assert res.ok, res.problems
    assert res.attributions["vendor"].counts()["vendor"] == 5
    hub = res.attributions["hub"]
    assert {c.spoke for c in hub.changes} == {"finance_spoke", "logistics_spoke"}
    assert [c.test_id for c in res.attributions["spoke"].changes] == ["fin-012"]
    # the source project is never modified
    assert sorted(p.read_bytes() for p in (src / "lookml").rglob("*.lkml")) == before


def test_reports_are_self_contained(example_copy: Path, small_seed: Path, tmp_path: Path) -> None:
    src = _source(example_copy, small_seed)
    res = run_demo(src, tmp_path / "demo", log=lambda _: None)
    md_path, html_path = res.reports["hub"]
    html = html_path.read_text()
    assert "<svg" in html and "Attribution vs run-0001" in html
    # offline: no external scripts, stylesheets, fonts or images
    assert not re.search(r"""(src|href)\s*=\s*["']https?://""", html)
    assert "@import" not in html
    md = md_path.read_text()
    assert md.startswith("## lkagent verify") and "| **hub** |" in md
    assert len(md) < 65000  # fits a GitHub PR comment


def test_cli_attribute_and_report(example_copy: Path, small_seed: Path, tmp_path: Path) -> None:
    src = _source(example_copy, small_seed)
    runner = CliRunner()
    assert runner.invoke(app, ["verify", "-c", str(src), "--profile", "v1"]).exit_code == 0
    r = runner.invoke(app, ["verify", "-c", str(src), "--profile", "v3_instruction_override"])
    assert r.exit_code == 1  # failures gate CI
    out = runner.invoke(app, ["attribute", "-c", str(src)])
    assert (
        out.exit_code == 0
        and "vendor" in out.output
        and "rule:vocabulary" not in out.output.split("\n")[0]
    )
    rep = runner.invoke(app, ["report", "-c", str(src), "--out-dir", str(tmp_path / "r")])
    assert rep.exit_code == 0 and (tmp_path / "r" / "report.html").exists()
