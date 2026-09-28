from __future__ import annotations

import json
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from lookml_agentops.cli import app
from lookml_agentops.config import load_config
from lookml_agentops.demo import run_demo
from lookml_agentops.diagnose.bundle import build_bundle
from lookml_agentops.diagnose.run import RunOptions, run_diagnose
from lookml_agentops.diagnose.why import diagnose
from lookml_agentops.inputs.declared import load_owners


def test_demo_four_attributions(harbor: Path, tmp_path: Path) -> None:
    res = run_demo(harbor, tmp_path / "demo", log=lambda _: None)
    assert res.ok, res.problems
    ext = res.diagnoses["external"]
    assert sorted(v.test_id for v in ext.verdicts) == [
        "log-002",
        "log-004",
        "log-005",
        "log-009",
        "log-015",
    ]
    lookml = res.diagnoses["lookml"]
    assert res.impact is not None and res.impact.test_ids() == {v.test_id for v in lookml.verdicts}
    assert len(lookml.verdicts) == 14
    v = next(v for v in lookml.verdicts if v.test_id == "fin-001")
    assert v.confidence == "high" and v.changes[0].change.params[0].param == "sql"
    spec = res.diagnoses["spec"]
    assert [v.test_id for v in spec.verdicts] == ["fin-020"]
    assert "rule:sales-means-net" in (spec.verdicts[0].likely_cause or "")
    assert res.locked_error and "LKS008" in res.locked_error
    data = res.diagnoses["data"]
    assert {v.cause for v in data.verdicts} == {"data"} and "fin-001" in {
        v.test_id for v in data.verdicts
    }
    # the source example is untouched
    assert (
        "COALESCE(${order_refund_facts.refund_amount}, 0)"
        in (harbor / "projects/core_project/views/orders.view.lkml").read_text()
    )


def test_multiple_inputs_rank_the_exercised_change_first(harbor: Path) -> None:
    cfg = load_config(harbor)
    a = run_diagnose(cfg, RunOptions(agents=["finance-analyst"], record=False))
    view = harbor / "projects/core_project/views/orders.view.lkml"
    view.write_text(
        view.read_text().replace(
            "sql: ${gross_amount} - ${discount_amount} - COALESCE(${order_refund_facts.refund_amount}, 0) ;;",
            "sql: ${gross_amount} ;;",
        )
    )
    glossary = harbor / "catalog/glossary.yaml"
    glossary.write_text(
        glossary.read_text().replace(
            "Gross order revenue minus discounts minus refunds", "Gross order revenue"
        )
    )
    b = run_diagnose(load_config(harbor), RunOptions(agents=["finance-analyst"], record=False))
    d = diagnose(a, b, load_owners(cfg))
    v = next(v for v in d.verdicts if v.test_id == "fin-001")
    assert v.cause == "multiple" and v.confidence == "low"
    assert (
        v.changes[0].element_id == "field:finance_project/orders.net_revenue"
    )  # rank 0 before the term
    assert {c.input_id for c in v.changes} == {"lookml:core_project", "catalog:glossary"}
    assert v.owner == "central-data-platform"


def test_bundle_is_deterministic_and_has_no_rows(harbor: Path) -> None:
    cfg = load_config(harbor)
    a = run_diagnose(cfg, RunOptions(agents=["logistics-ops"]))
    b = run_diagnose(cfg, RunOptions(agents=["logistics-ops"], scenario="fuzzy_values"))
    d = diagnose(a, b, load_owners(cfg))
    z1, z2 = build_bundle(a, b, d), build_bundle(a, b, d)
    assert z1 == z2
    with zipfile.ZipFile(__import__("io").BytesIO(z1)) as zf:
        names = zf.namelist()
        assert "README.md" in names and "inputs.json" in names
        t = json.loads(zf.read("tests/logistics-ops/log-002.json"))
        assert t["question"].startswith("How many shipments")
        assert t["answer_before"]["filters"] != t["answer_after"]["filters"]
        assert all(
            fp["unchanged"]
            for fp in t["dependency_fingerprints"]
            if not fp["element"].startswith("runner:")
        )
        inputs = json.loads(zf.read("inputs.json"))
        assert all(i["unchanged"] for i in inputs)
        for name in names:
            assert '"rows"' not in zf.read(name).decode()


def test_cli_why_impact_bundle(harbor: Path, tmp_path: Path) -> None:
    import shutil

    runner = CliRunner()
    base = tmp_path / "base"
    shutil.copytree(harbor, base, ignore=shutil.ignore_patterns("seed"))
    assert (
        runner.invoke(
            app, ["diagnose", "run", "-c", str(harbor), "--agent", "finance-analyst"]
        ).exit_code
        == 0
    )
    (harbor / "agents/finance-analyst.agent.md").write_text(
        (harbor / "agents/finance-analyst.agent.md")
        .read_text()
        .replace("mean net revenue.", "mean gross revenue.")
    )
    imp = runner.invoke(
        app, ["diagnose", "impact", "-c", str(harbor), "--base-config", str(base), "--run"]
    )
    assert imp.exit_code == 0, imp.output
    assert "rule:sales-means-net" in imp.output and "fin-020" in imp.output
    why = runner.invoke(app, ["diagnose", "why", "-c", str(harbor)])
    assert why.exit_code == 0 and "finance-analytics" in why.output
    out = tmp_path / "b.zip"
    res = runner.invoke(
        app,
        ["diagnose", "bundle", "run-0001", "run-0002", "-c", str(harbor), "-o", str(out), "--all"],
    )
    assert res.exit_code == 0 and out.exists()
