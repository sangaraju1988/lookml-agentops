"""Conversational Analytics API runner (``pip install lookml-agentops[ca]``).

Confirmed (v1 REST reference): ``POST {endpoint}/projects/{p}/locations/{l}:chat`` with
``messages[{userMessage{text}}]`` and ``dataAgentContext{dataAgent, contextVersion}``; the
response is a stream (JSON array) of ``Message`` objects whose ``systemMessage`` may hold
``text``, ``data`` (``generatedSql``, ``result{schema, data}``), ``error``.

TODO(verify-api):
* where Looker credentials go (we send top-level ``credentials.oauth.secret`` when
  LOOKER_CLIENT_ID/LOOKER_CLIENT_SECRET are set);
* where the generated Looker query lives in a data message (see answer_parse);
* the API does not appear to expose a model/version identifier, so vendor attribution relies
  on elimination (no recorded input changed).

Secrets are read from the environment only and never logged: LKAGENT_CA_ACCESS_TOKEN (Google
OAuth access token), GOOGLE_CLOUD_PROJECT, LOOKER_CLIENT_ID, LOOKER_CLIENT_SECRET.
"""

from __future__ import annotations

import os
from typing import Any

from lookml_agentops.config import CARunnerConfig, LkagentConfig
from lookml_agentops.verify.models import AgentAnswer, RunnerMeta, TestCase
from lookml_agentops.verify.runners.answer_parse import parse_ca_messages
from lookml_agentops.verify.runners.base import Runner, SpokeContext

API_VERSION = "geminidataanalytics/v1"


class RunnerConfigError(Exception):
    pass


class CARunner(Runner):
    def __init__(
        self,
        conf: CARunnerConfig,
        *,
        project: str,
        token: str,
        looker_client_id: str | None = None,
        looker_client_secret: str | None = None,
        transport: Any = None,
    ) -> None:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - optional extra
            raise RunnerConfigError(
                "install the [ca] extra: pip install lookml-agentops[ca]"
            ) from exc
        self.conf = conf
        self.project = project
        self._looker = (looker_client_id, looker_client_secret)
        self._client = httpx.Client(
            timeout=conf.timeout_seconds,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            transport=transport,
        )

    @classmethod
    def from_config(cls, cfg: LkagentConfig) -> CARunner:
        env = os.environ
        missing = [k for k in ("LKAGENT_CA_ACCESS_TOKEN", "GOOGLE_CLOUD_PROJECT") if not env.get(k)]
        if missing:
            raise RunnerConfigError(f"CA runner needs environment variables: {', '.join(missing)}")
        if not cfg.verify.ca.agents:
            raise RunnerConfigError("set verify.ca.agents (spoke -> data agent id) in lkagent.yaml")
        return cls(
            cfg.verify.ca,
            project=env["GOOGLE_CLOUD_PROJECT"],
            token=env["LKAGENT_CA_ACCESS_TOKEN"],
            looker_client_id=env.get("LOOKER_CLIENT_ID"),
            looker_client_secret=env.get("LOOKER_CLIENT_SECRET"),
        )

    @property
    def meta(self) -> RunnerMeta:
        return RunnerMeta(runner="ca", runner_version=API_VERSION, structured=True)

    def _agent_name(self, spoke: str) -> str:
        agent = self.conf.agents.get(spoke)
        if not agent:
            raise RunnerConfigError(f"no data agent configured for {spoke} (verify.ca.agents)")
        if agent.startswith("projects/"):
            return agent
        return f"projects/{self.project}/locations/{self.conf.location}/dataAgents/{agent}"

    def request_body(self, question: str, spoke: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "messages": [{"userMessage": {"text": question}}],
            "dataAgentContext": {
                "dataAgent": self._agent_name(spoke),
                "contextVersion": self.conf.context_version,
            },
        }
        cid, secret = self._looker
        if cid and secret:
            body["credentials"] = {"oauth": {"secret": {"clientId": cid, "clientSecret": secret}}}
        return body

    def answer(self, test: TestCase, ctx: SpokeContext) -> AgentAnswer:
        url = f"{self.conf.endpoint}/projects/{self.project}/locations/{self.conf.location}:chat"
        try:
            resp = self._client.post(url, json=self.request_body(test.question, ctx.spoke))
        except Exception as exc:  # network errors: report type only, never headers
            return AgentAnswer(meta=self.meta, error=f"request failed: {type(exc).__name__}")
        if resp.status_code >= 400:
            return AgentAnswer(meta=self.meta, error=f"HTTP {resp.status_code} from CA API")
        try:
            payload = resp.json()
        except ValueError:
            return AgentAnswer(meta=self.meta, error="CA API returned non-JSON response")
        messages = payload if isinstance(payload, list) else [payload]
        return parse_ca_messages([m for m in messages if isinstance(m, dict)], self.meta)

    def close(self) -> None:
        self._client.close()
