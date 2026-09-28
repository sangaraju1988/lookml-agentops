"""CA / MCP adapters, tested offline with synthetic responses shaped like the documented API."""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace
from typing import Any

import pytest
from tests.conftest import EXAMPLE

from lookml_agentops.config import CARunnerConfig, MCPRunnerConfig, load_config
from lookml_agentops.verify.models import Expectation, RunnerMeta, TestCase
from lookml_agentops.verify.runners.answer_parse import parse_ca_messages, parse_tool_payload
from lookml_agentops.verify.runners.base import SpokeContext

META = RunnerMeta(runner="ca", runner_version="x")
TOKEN = "ya29.secret-token-value"

DATA_MSG = {
    "systemMessage": {
        "data": {
            "generatedLookerQuery": {
                "model": "finance",
                "explore": "finance_orders",
                "fields": ["regions.region_name", "orders.net_revenue"],
                "filters": [
                    {"field": "orders.created_date", "value": "last quarter"},
                    {"field": "regions.region_name", "value": "Midwest,Mountain"},
                ],
            },
            "generatedSql": "SELECT ...",
            "result": {
                "schema": {
                    "fields": [
                        {"name": "orders.net_revenue", "type": "FLOAT"},
                        {"name": "regions.region_name", "type": "STRING"},
                    ]
                },
                "data": [{"orders.net_revenue": "10.5", "regions.region_name": "Midwest"}],
            },
        }
    }
}


def _ctx() -> SpokeContext:
    return SpokeContext("finance_spoke", None, None, dt.date(2026, 1, 20))  # type: ignore[arg-type]


def _test() -> TestCase:
    return TestCase(
        id="t",
        question="What was net revenue by region last quarter?",
        spoke="finance_spoke",
        expect=Expectation(),
    )


def test_parse_ca_structured_answer() -> None:
    ans = parse_ca_messages([{"userMessage": {"text": "q"}}, DATA_MSG], META)
    assert ans.meta.structured
    assert ans.explore == "finance_orders"
    assert ans.fields == ["regions.region_name", "orders.net_revenue"]
    date_f, region_f = ans.filters
    assert date_f.raw == "last quarter" and date_f.values == [] and date_f.start is None
    assert region_f.values == ["Midwest", "Mountain"]
    assert ans.rows == [["Midwest", 10.5]]  # dimensions first, numeric cast


def test_parse_ca_query_under_query_looker_and_result_only() -> None:
    alt = json.loads(json.dumps(DATA_MSG))
    alt["systemMessage"]["data"]["query"] = {
        "looker": alt["systemMessage"]["data"].pop("generatedLookerQuery")
    }
    assert parse_ca_messages([alt], META).explore == "finance_orders"
    bare = json.loads(json.dumps(DATA_MSG))
    del bare["systemMessage"]["data"]["generatedLookerQuery"]
    ans = parse_ca_messages([bare], META)
    assert not ans.meta.structured and ans.rows == [["Midwest", 10.5]]


def test_parse_ca_prose_only_is_refusal_and_errors() -> None:
    prose = {
        "systemMessage": {
            "text": {"parts": ["I can't share personal data."], "textType": "FINAL_RESPONSE"}
        }
    }
    ans = parse_ca_messages([prose], META)
    assert ans.refused and "personal data" in (ans.refusal_reason or "")
    err = parse_ca_messages([{"systemMessage": {"error": {"text": "explore not found"}}}], META)
    assert err.error == "explore not found"


def test_ca_runner_request_and_secret_hygiene(monkeypatch: pytest.MonkeyPatch) -> None:
    httpx = pytest.importorskip("httpx")
    from lookml_agentops.verify.runners.ca import CARunner, RunnerConfigError

    seen: dict[str, Any] = {}

    def handler(request: Any) -> Any:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=[{"userMessage": {"text": "q"}}, DATA_MSG])

    conf = CARunnerConfig(agents={"finance_spoke": "fin-agent"})
    r = CARunner(
        conf,
        project="demo-proj",
        token=TOKEN,
        looker_client_id="cid",
        looker_client_secret="csec",
        transport=httpx.MockTransport(handler),
    )
    ans = r.answer(_test(), _ctx())
    assert ans.explore == "finance_orders"
    assert (
        seen["url"]
        == "https://geminidataanalytics.googleapis.com/v1/projects/demo-proj/locations/global:chat"
    )
    assert seen["body"]["dataAgentContext"] == {
        "dataAgent": "projects/demo-proj/locations/global/dataAgents/fin-agent",
        "contextVersion": "PUBLISHED",
    }
    assert seen["body"]["messages"] == [{"userMessage": {"text": _test().question}}]

    def boom(request: Any) -> Any:
        return httpx.Response(403, text=f"denied for {TOKEN}")

    r2 = CARunner(conf, project="demo-proj", token=TOKEN, transport=httpx.MockTransport(boom))
    bad = r2.answer(_test(), _ctx())
    assert bad.error == "HTTP 403 from CA API" and TOKEN not in bad.model_dump_json()

    for k in ("LKAGENT_CA_ACCESS_TOKEN", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RunnerConfigError, match="LKAGENT_CA_ACCESS_TOKEN, GOOGLE_CLOUD_PROJECT"):
        CARunner.from_config(load_config(EXAMPLE))


def test_mcp_runner_parsing_and_args(monkeypatch: pytest.MonkeyPatch) -> None:
    from lookml_agentops.verify.runners.ca import RunnerConfigError
    from lookml_agentops.verify.runners.mcp import MCPRunner

    with pytest.raises(RunnerConfigError, match=r"verify\.mcp\.tool"):
        MCPRunner(MCPRunnerConfig(), url="http://localhost/mcp")
    conf = MCPRunnerConfig(
        tool="ask",
        question_arg="q",
        extra_args={"model": "m"},
        spoke_args={"finance_spoke": {"agent": "fin"}},
    )
    r = MCPRunner(conf, url="http://localhost/mcp", token=TOKEN)
    assert r.arguments("hi", "finance_spoke") == {"q": "hi", "model": "m", "agent": "fin"}
    text = SimpleNamespace(
        is_error=False,
        structured_content=None,
        content=[SimpleNamespace(text=json.dumps([DATA_MSG]))],
    )
    assert r.parse_result(text).explore == "finance_orders"
    prose = SimpleNamespace(
        is_error=False, structured_content=None, content=[SimpleNamespace(text="no idea")]
    )
    assert r.parse_result(prose).meta.structured is False
    assert r.parse_result(SimpleNamespace(is_error=True)).error == "MCP tool returned an error"
    monkeypatch.delenv("LKAGENT_MCP_URL", raising=False)
    with pytest.raises(RunnerConfigError, match="LKAGENT_MCP_URL"):
        MCPRunner.from_config(load_config(EXAMPLE))


def test_tool_payload_dict_shape() -> None:
    payload = {
        "explore": "e",
        "fields": ["v.m"],
        "filters": [],
        "data": [{"v.m": 3}],
        "schema": {"fields": [{"name": "v.m", "type": "INTEGER"}]},
    }
    ans = parse_tool_payload(payload, META)
    assert ans.explore == "e" and ans.rows == [[3]] and ans.meta.structured
