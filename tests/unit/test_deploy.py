"""Staged deploy against a mocked CA HTTP server (no network). Publishing is manual by design."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from lookml_agentops.cli import app
from lookml_agentops.config import load_config
from lookml_agentops.generate.deploy import (
    MANUAL_PUBLISH,
    CAAdminClient,
    deploy,
    deployment_history,
)

httpx = pytest.importorskip("httpx")
PUBLISHED = {"system_instruction": "live context"}


def _server(seen: list[dict[str, Any]]) -> Any:
    def handler(request: Any) -> Any:
        seen.append(
            {
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
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


def _deploy(cfg, seen, rate):  # type: ignore[no-untyped-def]
    return deploy(
        cfg,
        "finance-analyst",
        client=_client(seen),
        run_against_staging=lambda c, a: (rate, "run-0001"),
        looker_instance_uri="https://looker.example.com",
    )


def test_stage_preserves_live_context(example_copy: Path) -> None:
    seen: list[dict[str, Any]] = []
    res = _deploy(_cfg(example_copy), seen, 1.0)
    assert res.staged and res.ready_to_publish and MANUAL_PUBLISH in res.notes
    get, patch = seen  # exactly one read and one staging write, nothing else
    assert (
        get["method"] == "GET"
        and get["path"] == "/v1/projects/p/locations/global/dataAgents/fin-agent"
    )
    assert patch["path"].endswith("/dataAgents/fin-agent:updateSync")
    assert patch["params"] == {"updateMask": "data_analytics_agent"}
    daa = patch["body"]["data_analytics_agent"]
    assert daa["published_context"] == PUBLISHED  # live context sent back unchanged
    staged = daa["staging_context"]
    assert set(staged) == {"system_instruction", "looker_golden_queries", "datasource_references"}
    ref = staged["datasource_references"]["looker"]["explore_references"][0]
    assert ref["looker_instance_uri"] == "https://looker.example.com"


def test_gate_blocks_below_threshold_and_history_is_recorded(example_copy: Path) -> None:
    cfg = _cfg(example_copy)
    res = _deploy(cfg, [], 0.5)
    assert res.staged and not res.ready_to_publish
    assert "below the threshold" in res.notes[0]
    _deploy(cfg, [], 1.0)
    hist = deployment_history(cfg, "finance-analyst")
    assert [d["ready_to_publish"] for d in hist] == [False, True]
    assert all(d["run_id"] == "run-0001" and d["staged"] for d in hist)


def test_cli_rollback_prints_manual_guidance(
    example_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k in ("LKAGENT_CA_ACCESS_TOKEN", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(k, raising=False)
    runner = CliRunner()
    res = runner.invoke(app, ["generate", "deploy", "finance-analyst", "-c", str(example_copy)])
    assert res.exit_code == 2 and "LKAGENT_CA_ACCESS_TOKEN" in res.output
    res = runner.invoke(app, ["generate", "rollback", "finance-analyst", "-c", str(example_copy)])
    assert res.exit_code == 0
    assert (
        "Rollback is manual" in res.output
        and "no deployments of finance-analyst recorded" in res.output
    )
