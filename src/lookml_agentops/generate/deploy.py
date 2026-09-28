"""Staged deploy of compiled agent context to a Conversational Analytics data agent (``[ca]`` extra).

Confirmed (CA API v1 reference + build-agent-http doc):

* ``GET  {endpoint}/projects/{p}/locations/{l}/dataAgents/{id}`` returns the agent, including
  ``data_analytics_agent.{staging_context, published_context, last_published_context}``;
* ``PATCH …/dataAgents/{id}:updateSync?updateMask=data_analytics_agent`` updates it (snake_case
  JSON bodies are used in the HTTP guide);
* ``staging_context`` is "used to test and validate changes before publishing"; chat can target
  it with ``dataAgentContext.contextVersion: STAGING``;
* ``last_published_context`` is output-only.

Because the documented update mask is the whole ``data_analytics_agent`` object, staging always
sends the agent's *current* ``published_context`` back unchanged, so staging never touches
production.

Publishing and rollback are **manual by design**: the API reference documents no publish or
rollback operation (``last_published_context`` is output-only), so lkagent never writes the live
``published_context``. After a staged run passes the gate, a person publishes from the Looker /
Conversational Analytics UI, and rolls back there too. ``lkagent generate rollback`` prints that
guidance plus the recorded deployment history.

Credentials: LKAGENT_CA_ACCESS_TOKEN, GOOGLE_CLOUD_PROJECT, LOOKER_INSTANCE_URI (env only).
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lookml_agentops._util.hashing import canonical_json, sha256_text
from lookml_agentops._util.io import write_text
from lookml_agentops.config import LkagentConfig
from lookml_agentops.generate.compile import compile_agents
from lookml_agentops.generate.exporters.ca_api import (
    LOOKER_INSTANCE_URI_PLACEHOLDER,
    export_ca_context,
)


class DeployError(Exception):
    pass


def _pick(d: dict[str, Any], snake: str, camel: str) -> Any:
    return d.get(snake, d.get(camel))


class CAAdminClient:
    """Minimal DataAgent admin client over HTTP (httpx)."""

    def __init__(
        self,
        *,
        endpoint: str,
        project: str,
        location: str,
        token: str,
        transport: Any = None,
        timeout: float = 120.0,
    ) -> None:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - optional extra
            raise DeployError("install the [ca] extra: pip install 'lookml-agentops[ca]'") from exc
        self.base = f"{endpoint.rstrip('/')}/projects/{project}/locations/{location}/dataAgents"
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )

    def _url(self, agent: str) -> str:
        if agent.startswith("projects/"):
            return f"{self.base.split('/projects/')[0]}/{agent}"
        return f"{self.base}/{agent}"

    def get(self, agent: str) -> dict[str, Any]:
        resp = self._client.get(self._url(agent))
        if resp.status_code >= 400:
            raise DeployError(f"get data agent {agent}: HTTP {resp.status_code}")
        data = resp.json()
        if not isinstance(data, dict):
            raise DeployError("unexpected response from get data agent")
        return data

    def update_staging(
        self, agent: str, context: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        daa = _pick(current, "data_analytics_agent", "dataAnalyticsAgent") or {}
        published = _pick(daa, "published_context", "publishedContext")
        body_daa: dict[str, Any] = {"staging_context": context}
        if published is not None:
            body_daa["published_context"] = published  # preserved: the mask covers the whole object
        resp = self._client.patch(
            f"{self._url(agent)}:updateSync",
            params={"updateMask": "data_analytics_agent"},
            json={"data_analytics_agent": body_daa},
        )
        if resp.status_code >= 400:
            raise DeployError(f"update staging for {agent}: HTTP {resp.status_code}")
        data = resp.json()
        return data if isinstance(data, dict) else {}

    def close(self) -> None:
        self._client.close()


MANUAL_PUBLISH = (
    "Publishing is manual: open the data agent in the Looker / Conversational Analytics UI, review "
    "the staged instructions, and publish them there. lkagent never changes the live (published) "
    "context."
)
MANUAL_ROLLBACK = (
    "Rollback is manual: re-stage the last good spec (`git checkout <commit> -- agents/`, then "
    "`lkagent generate deploy <agent>`) and publish it from the Looker / Conversational Analytics "
    "UI, or put back the previous instructions directly in the UI."
)


@dataclass
class DeployResult:
    agent: str
    remote: str
    staged: bool = False
    pass_rate: float | None = None
    threshold: float = 1.0
    run_id: str | None = None
    context_hash: str = ""
    spec_hash: str = ""
    ready_to_publish: bool = False  # staged tests met the threshold; publish manually in the UI
    notes: list[str] = field(default_factory=list)


def context_for(
    cfg: LkagentConfig, agent: str, looker_instance_uri: str
) -> tuple[dict[str, Any], str]:
    agents = compile_agents(cfg, agent)
    if agent not in agents:
        raise DeployError(
            f"{agent!r} is abstract or unknown; only agents with explores can be deployed"
        )
    text, warnings = export_ca_context(agents[agent].spec)
    if warnings:
        raise DeployError("; ".join(warnings))
    text = text.replace(json.dumps(LOOKER_INSTANCE_URI_PLACEHOLDER)[1:-1], looker_instance_uri)
    return json.loads(text), agents[agent].spec.content_hash


def client_from_env(cfg: LkagentConfig, transport: Any = None) -> CAAdminClient:
    env = os.environ
    missing = [k for k in ("LKAGENT_CA_ACCESS_TOKEN", "GOOGLE_CLOUD_PROJECT") if not env.get(k)]
    if missing:
        raise DeployError(f"deploy needs environment variables: {', '.join(missing)}")
    conf = cfg.diagnose.ca
    return CAAdminClient(
        endpoint=conf.endpoint,
        project=env["GOOGLE_CLOUD_PROJECT"],
        location=conf.location,
        token=env["LKAGENT_CA_ACCESS_TOKEN"],
        transport=transport,
        timeout=conf.timeout_seconds,
    )


def record_deployment(cfg: LkagentConfig, res: DeployResult) -> Path:
    path = cfg.path(".lkagent/deployments.json")
    data: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {"deployments": []}
    data["deployments"].append(
        {
            "agent": res.agent,
            "remote": res.remote,
            "spec_hash": res.spec_hash,
            "context_hash": res.context_hash,
            "staged": res.staged,
            "ready_to_publish": res.ready_to_publish,
            "pass_rate": res.pass_rate,
            "threshold": res.threshold,
            "run_id": res.run_id,
            "at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
        }
    )
    write_text(path, canonical_json(data))
    return path


def deploy(
    cfg: LkagentConfig,
    agent: str,
    *,
    client: CAAdminClient,
    run_against_staging: Any,  # Callable[[LkagentConfig, str], tuple[float, str]] -> (pass rate, run id)
    looker_instance_uri: str | None = None,
) -> DeployResult:
    """Stage the compiled context, test it against staging and apply the pass-rate gate.

    The live (published) context is never modified; publishing is done by a person in the UI.
    """
    remote = cfg.diagnose.ca.agents.get(agent)
    if not remote:
        raise DeployError(f"no CA data agent configured for {agent} (diagnose.ca.agents)")
    uri = looker_instance_uri or os.environ.get("LOOKER_INSTANCE_URI")
    if not uri:
        raise DeployError("set LOOKER_INSTANCE_URI (the Looker instance the data agent reads)")
    context, spec_hash = context_for(cfg, agent, uri)
    res = DeployResult(
        agent=agent,
        remote=remote,
        threshold=cfg.diagnose.deploy.pass_threshold,
        spec_hash=spec_hash,
        context_hash=sha256_text(canonical_json(context))[:16],
    )
    current = client.get(remote)
    client.update_staging(remote, context, current)
    res.staged = True
    res.pass_rate, res.run_id = run_against_staging(cfg, agent)
    if res.pass_rate < res.threshold:
        res.notes.append(
            f"pass rate {res.pass_rate:.1%} against staging is below the threshold "
            f"{res.threshold:.1%}; do not publish this version"
        )
    else:
        res.ready_to_publish = True
        res.notes.append(MANUAL_PUBLISH)
    record_deployment(cfg, res)
    return res


def deployment_history(cfg: LkagentConfig, agent: str) -> list[dict[str, Any]]:
    path = cfg.path(".lkagent/deployments.json")
    if not path.exists():
        return []
    return [
        d for d in json.loads(path.read_text()).get("deployments", []) if d.get("agent") == agent
    ]


def staging_runner(cfg: LkagentConfig, agent: str) -> tuple[float, str]:
    """Run `diagnose run` for one agent through the CA runner against the STAGING context."""
    from lookml_agentops.diagnose.run import RunOptions, run_diagnose

    staged = cfg.model_copy(deep=True)
    staged.root, staged.source = cfg.root, cfg.source
    staged.diagnose.ca.context_version = "STAGING"
    rec = run_diagnose(
        staged, RunOptions(runner="ca", agents=[agent], label=f"deploy {agent} (staging)")
    )
    checkable = [r for r in rec.results if r.status != "skipped"]
    ok = sum(r.status in ("pass", "drift") for r in checkable)
    return (ok / len(checkable) if checkable else 0.0), rec.info.run_id
