from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import EXAMPLE, FIXTURES, REPO
from typer.testing import CliRunner

from lookml_agentops.cli import app
from lookml_agentops.config import RuleOverride, load_config
from lookml_agentops.lint.docs import render_rules_markdown
from lookml_agentops.lint.engine import run_lint
from lookml_agentops.lint.reporters.markdown import render_markdown
from lookml_agentops.lint.reporters.sarif import render_sarif
from lookml_agentops.lint.rules import ALL_RULES

BROKEN = FIXTURES / "broken_hub"


def _snapshot(result) -> str:  # type: ignore[no-untyped-def]
    return "".join(f"{f.rule_id} {f.severity} {f.loc or '-'} {f.object}\n" for f in result.findings)


def test_example_project_is_clean() -> None:
    result = run_lint(load_config(EXAMPLE))
    assert result.findings == []


def test_broken_fixture_matches_snapshot_exactly() -> None:
    result = run_lint(load_config(BROKEN))
    assert _snapshot(result) == (BROKEN / "expected_findings.txt").read_text(encoding="utf-8")


def test_broken_fixture_triggers_every_rule() -> None:
    result = run_lint(load_config(BROKEN))
    assert {f.rule_id for f in result.findings} == {r.id for r in ALL_RULES}


def test_rule_ids_unique_and_documented() -> None:
    ids = [r.id for r in ALL_RULES]
    assert len(ids) == len(set(ids))
    for r in ALL_RULES:
        assert r.rationale and r.fix_hint and r.name


def test_config_can_disable_and_override_severity() -> None:
    cfg = load_config(BROKEN)
    cfg.lint.rules["LKA004"] = RuleOverride(enabled=False)
    cfg.lint.rules["LKA009"] = RuleOverride(severity="error")
    result = run_lint(cfg)
    assert not any(f.rule_id == "LKA004" for f in result.findings)
    assert {f.severity for f in result.findings if f.rule_id == "LKA009"} == {"error"}


def test_exemption_with_reason_suppresses_finding() -> None:
    # examples: shipments.delivered is exempt from LKA010; removing the exemption must surface it
    cfg = load_config(EXAMPLE)
    result = run_lint(cfg)
    assert not any(f.rule_id == "LKA010" for f in result.findings)
    from lookml_agentops.lint.engine import build_context

    ctx = build_context(cfg)
    for em in ctx.models.values():
        if "shipments" in em.views:
            em.views["shipments"].fields["delivered"].exemptions.clear()
    result = run_lint(cfg, ctx)
    assert any(f.rule_id == "LKA010" and "delivered" in f.message for f in result.findings)


def test_exemption_without_reason_is_reported() -> None:
    result = run_lint(load_config(BROKEN))
    assert any(f.rule_id == "LKA000" and "no reason" in f.message for f in result.findings)
    # ...but it still suppresses the targeted rule
    assert not any(
        f.rule_id == "LKA001" and "hidden_but_exposed" in f.message for f in result.findings
    )


def test_sarif_shape() -> None:
    cfg = load_config(BROKEN)
    doc = render_sarif(run_lint(cfg), cfg, REPO)
    assert doc["version"] == "2.1.0"
    run = doc["runs"][0]
    assert {r["id"] for r in run["tool"]["driver"]["rules"]} == {r.id for r in ALL_RULES}
    uris = {
        loc["physicalLocation"]["artifactLocation"]["uri"]
        for res in run["results"]
        for loc in res.get("locations", [])
    }
    assert "tests/fixtures/broken_hub/lookml/core_hub/views/customers.view.lkml" in uris
    assert all((REPO / u).exists() for u in uris)


def test_markdown_report() -> None:
    md = render_markdown(run_lint(load_config(BROKEN)))
    assert md.startswith("## lkagent lint")
    assert "| 🔴 | LKA005 |" in md


def test_rules_doc_is_current() -> None:
    assert (REPO / "docs" / "lint-rules.md").read_text(encoding="utf-8") == render_rules_markdown()


def test_cli_exit_codes(tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["lint", "-c", str(EXAMPLE)]).exit_code == 0
    res = runner.invoke(
        app, ["lint", "-c", str(BROKEN), "-f", "sarif", "-o", str(tmp_path / "x.sarif")]
    )
    assert res.exit_code == 1
    json.loads((tmp_path / "x.sarif").read_text())
