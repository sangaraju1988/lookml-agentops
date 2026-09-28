from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import EXAMPLE
from typer.testing import CliRunner

from lookml_agentops.cli import app
from lookml_agentops.compile.build import check_build, compile_all
from lookml_agentops.compile.generate import CompileError
from lookml_agentops.compile.polish import PolishError, apply_polish
from lookml_agentops.config import load_config


def _read_all(d: Path) -> dict[str, bytes]:
    return {
        p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()
    }


def test_recompile_is_zero_diff(tmp_path: Path) -> None:
    cfg = load_config(EXAMPLE)
    compile_all(cfg, tmp_path / "a")
    compile_all(cfg, tmp_path / "b")
    assert _read_all(tmp_path / "a") == _read_all(tmp_path / "b")


def test_committed_build_is_current() -> None:
    assert check_build(load_config(EXAMPLE)) == []


def test_layers_ordered_and_every_rule_has_a_test(tmp_path: Path) -> None:
    res = compile_all(load_config(EXAMPLE), tmp_path)
    for spoke, instr in res.agents.items():
        assert [layer.role for layer in instr.layers] == ["hub", "spoke"]
        assert instr.layers[1].project == spoke
        for r in instr.all_rules():
            assert r.test_ids, f"{spoke}: rule {r.rule_id} has no adherence test"
        kinds = {r.kind for r in instr.all_rules()}
        assert (
            kinds
            == {
                "metric_definition",
                "default_filter",
                "exclusion",
                "time_convention",
                "pii_guardrail",
                "vocabulary",
            }
            or spoke == "logistics_spoke"
        )


def test_refined_hub_measure_stays_in_hub_layer(tmp_path: Path) -> None:
    res = compile_all(load_config(EXAMPLE), tmp_path)
    fin = res.agents["finance_spoke"]
    hub_ids = {r.rule_id for r in fin.layers[0].rules}
    spoke_ids = {r.rule_id for r in fin.layers[1].rules}
    assert "metric.orders.gross_revenue" in hub_ids
    assert "metric.orders.refund_rate" in spoke_ids
    rule = next(r for r in fin.layers[0].rules if r.rule_id == "metric.orders.net_revenue")
    assert rule.provenance.project == "core_hub"
    assert rule.provenance.file == "views/orders.view.lkml"
    assert rule.provenance.glossary_term_id == "net_revenue"
    # spoke wording change lands in spoke-layer field guidance
    assert any(g.field == "orders.gross_revenue" for g in fin.layers[1].field_guidance)


def _layer_hashes(cfg_dir: Path) -> dict[str, dict[str, str]]:
    res = compile_all(load_config(cfg_dir), cfg_dir / "build")
    return {s: a.layer_hashes() for s, a in res.agents.items()}


def test_hub_edit_changes_only_hub_layers(example_copy: Path) -> None:
    before = _layer_hashes(example_copy)
    p = example_copy / "lookml/core_hub/views/orders.view.lkml"
    p.write_text(
        p.read_text().replace(
            "Gross revenue minus discounts minus refunds", "Gross revenue minus discounts"
        )
    )
    after = _layer_hashes(example_copy)
    for spoke in before:
        assert before[spoke][f"spoke:{spoke}"] == after[spoke][f"spoke:{spoke}"]
        assert before[spoke]["hub:core_hub"] != after[spoke]["hub:core_hub"]


def test_spoke_edit_changes_only_that_spoke_layer(example_copy: Path) -> None:
    before = _layer_hashes(example_copy)
    p = example_copy / "lookml/finance_spoke/views/finance_refinements.view.lkml"
    p.write_text(p.read_text().replace("divided by gross revenue", "divided by gross revenue (v2)"))
    after = _layer_hashes(example_copy)
    assert before["finance_spoke"]["hub:core_hub"] == after["finance_spoke"]["hub:core_hub"]
    assert (
        before["finance_spoke"]["spoke:finance_spoke"]
        != after["finance_spoke"]["spoke:finance_spoke"]
    )
    assert before["logistics_spoke"] == after["logistics_spoke"]


def test_spoke_redefining_certified_measure_fails(example_copy: Path) -> None:
    p = example_copy / "lookml/finance_spoke/views/finance_refinements.view.lkml"
    p.write_text(
        p.read_text() + "\nview: +orders {\n  measure: net_revenue {\n"
        "    sql: ${gross_amount} ;;\n  }\n}\n"
    )
    with pytest.raises(
        CompileError, match=r"contradicts hub certified rule metric\.orders\.net_revenue"
    ):
        compile_all(load_config(example_copy), example_copy / "build")


def test_spoke_vocabulary_contradicting_hub_fails(example_copy: Path) -> None:
    g = example_copy / "catalog/glossary.yaml"
    g.write_text(
        g.read_text()
        + (
            "  - id: finance_revenue\n    name: Finance Revenue\n    definition: Refund-adjusted rate.\n"
            "    synonyms: [revenue]\n    linked_fields: [finance_spoke.orders.refund_rate]\n"
            "    status: approved\n"
        )
    )
    with pytest.raises(CompileError, match="vocab:revenue"):
        compile_all(load_config(example_copy), example_copy / "build")


def test_ca_export_uses_confirmed_fields(tmp_path: Path) -> None:
    compile_all(load_config(EXAMPLE), tmp_path)
    body = json.loads((tmp_path / "finance_spoke/ca_agent.json").read_text())
    assert set(body) == {"displayName", "description", "labels", "dataAnalyticsAgent"}
    ctx = body["dataAnalyticsAgent"]["stagingContext"]
    assert set(ctx) == {
        "systemInstruction",
        "datasourceReferences",
        "glossaryTerms",
        "lookerGoldenQueries",
    }
    refs = ctx["datasourceReferences"]["looker"]["exploreReferences"]
    assert {r["explore"] for r in refs} == {"finance_orders", "finance_invoices"}
    assert all(set(r) == {"lookerInstanceUri", "lookmlModel", "explore"} for r in refs)
    gq = ctx["lookerGoldenQueries"][0]
    assert set(gq) == {"naturalLanguageQuestions", "lookerQuery"}
    assert gq["lookerQuery"]["explore"] in {"finance_orders", "finance_invoices"}
    assert "[metric.orders.net_revenue]" in ctx["systemInstruction"]


class _Upper:
    def polish(self, rule_id: str, text: str) -> str:
        return text.upper()


def test_polish_may_only_change_prose(tmp_path: Path) -> None:
    instr = compile_all(load_config(EXAMPLE), tmp_path).agents["finance_spoke"]
    polished = apply_polish(instr, _Upper())
    assert [r.rule_id for r in polished.all_rules()] == [r.rule_id for r in instr.all_rules()]
    assert polished.all_rules()[0].text.isupper()
    assert instr.all_rules()[0].text != polished.all_rules()[0].text  # original untouched

    class _Sneaky:
        def polish(self, rule_id: str, text: str) -> str:
            return ""

    with pytest.raises(PolishError):
        apply_polish(instr, _Sneaky())


def test_cli_compile_check(tmp_path: Path, example_copy: Path) -> None:
    runner = CliRunner()
    res = runner.invoke(app, ["compile", "-c", str(example_copy), "--check"])
    assert res.exit_code == 1  # nothing compiled yet in the copy
    assert runner.invoke(app, ["compile", "-c", str(example_copy)]).exit_code == 0
    assert runner.invoke(app, ["compile", "-c", str(example_copy), "--check"]).exit_code == 0


def test_json_schema_doc_is_current() -> None:
    import json as _json

    from tests.conftest import REPO

    from lookml_agentops.compile.schema import AgentInstructions

    doc = (REPO / "docs/agent_instructions.v1.schema.json").read_text()
    assert (
        doc == _json.dumps(AgentInstructions.model_json_schema(), indent=2, sort_keys=True) + "\n"
    )
