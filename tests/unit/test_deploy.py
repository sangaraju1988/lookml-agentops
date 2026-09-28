"""Staged deploy against a mocked CA HTTP server (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from lookml_agentops.cli import app
from lookml_agentops.config import load_config
from lookml_agentops.generate.deploy import CAAdminClient, PublishNotConfirmed, deploy

httpx = pytest.importorskip("httpx")
PUBLISHED = {"system_instruction": "live context"}


def _server(seen: list[dict[str, Any]]) -> Any:
    def handler(request: Any) -> Any:
        seen.append(
            {
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
                "auth": request.headers.get("Authorization"),
                "body": json.loads(request.content) if request.content else None,
            }
        )
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "name": "projects/p/locations/global/dataAgents/fin-agent",
                    "dataAnalyticsAgent": {"publishedContext": PUBLISHED},
                },
            )
        return httpx.Response(
            200, json={"name": "projects/p/locations/global/dataAgents/fin-agent"}
        )

    return httpx.MockTransport(handler)


def _cfg(example_copy: Path):  # type: ignore[no-untyped-def]
    cfg = load_config(example_copy)
    cfg.diagnose.ca.agents = {"finance-analyst": "fin-agent"}
    return cfg


def _client(seen: list[dict[str, Any]]) -> CAAdminClient:
    return CAAdminClient(
        endpoint="https://geminidataanalytics.googleapis.com/v1",
        project="p",
        location="global",
        token="tok",
        transport=_server(seen),
    )


def test_stage_preserves_published_context_and_gates(example_copy: Path) -> None:
    seen: list[dict[str, Any]] = []
    cfg = _cfg(example_copy)
    res = deploy(
        cfg,
        "finance-analyst",
        client=_client(seen),
        run_against_staging=lambda c, a: (0.5, "run-0001"),
        looker_instance_uri="https://looker.example.com",
    )
    assert res.staged and not res.published and res.pass_rate == 0.5
    assert "below the threshold" in res.notes[0]
    get, patch = seen
    assert (
        get["method"] == "GET"
        and get["path"] == "/v1/projects/p/locations/global/dataAgents/fin-agent"
    )
    assert patch["path"].endswith("/dataAgents/fin-agent:updateSync")
    assert patch["params"] == {"updateMask": "data_analytics_agent"}
    daa = patch["body"]["data_analytics_agent"]
    assert daa["published_context"] == PUBLISHED  # production untouched
    staged = daa["staging_context"]
    assert set(staged) == {"system_instruction", "looker_golden_queries", "datasource_references"}
    ref = staged["datasource_references"]["looker"]["explore_references"][0]
    assert ref["looker_instance_uri"] == "https://looker.example.com"
    record = json.loads((example_copy / ".lkagent/deployments.json").read_text())["deployments"][-1]
    assert record["staged"] and not record["published"] and record["run_id"] == "run-0001"


def test_publish_is_refused_until_the_api_is_confirmed(example_copy: Path) -> None:
    seen: list[dict[str, Any]] = []
    with pytest.raises(PublishNotConfirmed, match="no publish or rollback operation"):
        deploy(
            _cfg(example_copy),
            "finance-analyst",
            client=_client(seen),
            run_against_staging=lambda c, a: (1.0, "run-0001"),
            looker_instance_uri="https://looker.example.com",
        )
    assert [s["method"] for s in seen] == ["GET", "PATCH"]  # staged, nothing else written


def test_cli_rollback_and_missing_credentials(
    example_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k in ("LKAGENT_CA_ACCESS_TOKEN", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(k, raising=False)
    runner = CliRunner()
    res = runner.invoke(app, ["generate", "deploy", "finance-analyst", "-c", str(example_copy)])
    assert res.exit_code == 2 and "LKAGENT_CA_ACCESS_TOKEN" in res.output
    res = runner.invoke(app, ["generate", "rollback", "finance-analyst", "-c", str(example_copy)])
    assert res.exit_code == 2  # no agent mapping in the example config
