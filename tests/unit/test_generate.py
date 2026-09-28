from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import EXAMPLE

from lookml_agentops.config import load_config
from lookml_agentops.generate.compile import (
    CompileError,
    bind_all,
    check_build,
    compile_agents,
    write_build,
)
from lookml_agentops.lint.engine import run_lint

HEAD = "---\nagent: {aid}\ndescription: test agent\n{extra}---\n"


def _spec(root: Path, aid: str, body: str = "", extra: str = "") -> Path:
    p = root / "agents" / f"{aid}.agent.md"
    p.write_text(HEAD.format(aid=aid, extra=extra) + body, encoding="utf-8")
    return p


def _rule_ids(root: Path, agent: str) -> set[str]:
    return {
        f.rule_id
        for f in bind_all(load_config(root)).findings
        if f.obj.startswith(agent) or agent in f.file
    }


def test_example_lints_clean_and_compiles() -> None:
    assert run_lint(load_config(EXAMPLE)).findings == []
    agents = compile_agents(load_config(EXAMPLE))
    assert sorted(agents) == [
        "finance-analyst",
        "logistics-ops",
        "procurement-buyer",
    ]  # baseline is abstract


def test_too_many_explores_and_unknown_explore(example_copy: Path) -> None:
    explores = "\n".join(
        f"  - {e}"
        for e in [
            "finance_project::finance_orders",
            "finance_project::finance_invoices",
            "logistics_project::logistics_shipments",
            "procurement_project::purchase_orders",
            "core_project::orders_base",
            "finance_project::nope",
        ]
    )
    _spec(example_copy, "wide", extra=f"explores:\n{explores}\n")
    ids = _rule_ids(example_copy, "wide")
    assert {"LKS003", "LKS002"} <= ids


def test_pivots_foreign_explore_and_unknown_fields(example_copy: Path) -> None:
    _spec(
        example_copy,
        "gq",
        extra="explores: [finance_project::finance_orders]\n",
        body="""
## Guardrails
- Never return customer email, phone, date of birth, billing address or contact name.

## Vocabulary
- "widgets" → finance_orders.no_such_field

## Golden queries
- id: pivoted
  questions: [q1]
  looker_query: {model: finance, explore: finance_orders, fields: [orders.net_revenue], pivots: [orders.channel]}
- id: foreign
  questions: [q2]
  looker_query: {model: finance, explore: finance_invoices, fields: [invoices.recognized_revenue]}
- id: typo
  questions: [q3]
  looker_query: {model: finance, explore: finance_orders, fields: [orders.net_revenu]}
""",
    )
    findings = [f for f in bind_all(load_config(example_copy)).findings if "gq.agent.md" in f.file]
    by_rule = {(f.rule_id, f.obj) for f in findings}
    assert ("LKS005", "pivoted") in by_rule
    assert ("LKS006", "foreign") in by_rule
    assert ("LKS004", "typo") in by_rule
    assert any(r == "LKS004" and o.startswith("gq-widgets") for r, o in by_rule)
    with pytest.raises(CompileError):
        compile_agents(load_config(example_copy), "gq")


def test_missing_pii_guardrail(example_copy: Path) -> None:
    _spec(example_copy, "leaky", extra="explores: [finance_project::finance_orders]\n")
    findings = [f for f in bind_all(load_config(example_copy)).findings if "leaky" in f.file]
    assert [f.rule_id for f in findings] == ["LKS010"]
    assert "customers.email" in findings[0].obj
    # the lint command reports it with the same rule id and a file:line location
    lint = [f for f in run_lint(load_config(example_copy)).findings if f.rule_id == "LKS010"]
    assert lint and str(lint[0].loc) == "agents/leaky.agent.md:1"


def test_locked_rule_vs_vocabulary_and_placement_advice(example_copy: Path) -> None:
    _spec(
        example_copy,
        "rebel",
        extra=(
            "extends: [shared/company-baseline.agent.md]\nexplores: [finance_project::finance_orders]\n"
        ),
        body="""
## Vocabulary
- "revenue" → finance_orders.gross_revenue
- "bookings" → finance_orders.gross_revenue
""",
    )
    ids = [f.rule_id for f in bind_all(load_config(example_copy)).findings if "rebel" in f.file]
    assert "LKS008" in ids  # contradicts locked revenue-default
    assert "LKS009" in ids  # "bookings" is already a catalog synonym of gross revenue


def test_recompile_is_zero_diff(tmp_path: Path) -> None:
    cfg = load_config(EXAMPLE)
    write_build(cfg, tmp_path / "a")
    write_build(cfg, tmp_path / "b")
    files_a = {
        p.relative_to(tmp_path / "a"): p.read_bytes()
        for p in (tmp_path / "a").rglob("*")
        if p.is_file()
    }
    files_b = {
        p.relative_to(tmp_path / "b"): p.read_bytes()
        for p in (tmp_path / "b").rglob("*")
        if p.is_file()
    }
    assert files_a == files_b
    assert check_build(cfg) == []


def test_generated_tests_carry_spec_element_ids() -> None:
    agents = compile_agents(load_config(EXAMPLE))
    for aid, ca in agents.items():
        elements = set(ca.spec.element_hashes())
        for t in ca.tests:
            assert t.rule_ids and set(t.rule_ids) <= elements, (aid, t.id)
        exercised = {e for t in ca.tests for e in t.rule_ids}
        assert {f"rule:{r.rule_id}" for r in ca.spec.active_rules()} <= exercised
        assert {f"golden:{q.golden_id}" for q in ca.spec.golden_queries} <= exercised


def test_exporters() -> None:
    build = EXAMPLE / "build" / "finance-analyst"
    ctx = json.loads((build / "ca_context.json").read_text())
    assert set(ctx) == {"system_instruction", "looker_golden_queries", "datasource_references"}
    gq = ctx["looker_golden_queries"][0]
    assert set(gq) == {"natural_language_questions", "looker_query"}
    assert {"model", "explore", "fields"} <= set(gq["looker_query"])
    import yaml

    si = yaml.safe_load(ctx["system_instruction"])
    assert set(si) == {"system_instruction", "glossaries", "additional_descriptions"}
    assert any("[revenue-default]" in d["text"] for d in si["additional_descriptions"])
    ui = (build / "looker_ui.md").read_text()
    assert "## Does not translate" in ui and "needs an Explore URL" in ui
    spec = json.loads((build / "agent_spec.json").read_text())
    rule = next(r for r in spec["rules"] if r["rule_id"] == "revenue-default")
    assert rule["source"] == {"file": "agents/shared/company-baseline.agent.md", "line": 7}


def test_resolve_golden_with_mocked_looker(
    example_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    httpx = pytest.importorskip("httpx")
    from lookml_agentops.generate.golden_resolve import LookerClient, resolve_golden, slug_from_url

    url = "https://looker.example.com/explore/finance/finance_orders?qid=AbC123"
    assert slug_from_url(url) == "AbC123"
    assert slug_from_url("https://looker.example.com/x/Zz9") == "Zz9"
    _spec(
        example_copy,
        "urly",
        extra="explores: [finance_project::finance_orders]\n",
        body=f"""
## Guardrails
- Never return customer email, phone, date of birth, billing address or contact name.

## Golden queries
- id: by-url
  questions: [Net revenue by channel]
  explore_url: {url}
""",
    )
    cfg = load_config(example_copy)
    assert "LKS012" in _rule_ids(example_copy, "urly")

    def handler(request: Any) -> Any:
        if request.url.path == "/api/4.0/login":
            return httpx.Response(200, json={"access_token": "t0k"})
        assert request.headers["Authorization"] == "Bearer t0k"
        assert request.url.path == "/api/4.0/queries/slug/AbC123"
        return httpx.Response(
            200,
            json={
                "model": "finance",
                "view": "finance_orders",
                "fields": ["orders.channel", "orders.net_revenue"],
                "filters": {"orders.created_date": "last quarter"},
                "sorts": [],
            },
        )

    client = LookerClient(
        "https://looker.example.com", "id", "secret", transport=httpx.MockTransport(handler)
    )
    report = resolve_golden(cfg, client)
    assert report["resolved"] == [url]
    ca = compile_agents(load_config(example_copy), "urly")["urly"]
    q = ca.spec.golden_queries[0]
    assert q.resolved_from == "url-cache" and q.looker_query is not None
    assert q.looker_query.explore == "finance_orders"
    assert "LKS012" not in _rule_ids(example_copy, "urly")
