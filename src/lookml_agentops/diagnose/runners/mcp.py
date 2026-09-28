"""MCP runner for the Looker managed MCP server or MCP Toolbox (``[mcp]`` extra).

Confirmed: the managed server is at ``<LOOKER_INSTANCE_URL>/mcp`` over HTTP with OAuth 2.1
(PKCE); admins enable tools individually. Tool names/schemas are not documented on that page.

TODO(verify-api): which tool answers natural-language questions and its argument names. This
runner therefore calls the tool named in ``diagnose.mcp.tool`` with ``{question_arg: question}``
plus configured extra args, and parses the result generically (structured content or JSON text).
If no Looker query structure is returned, comparison is result-only.

Environment: LKAGENT_MCP_URL (required), LKAGENT_MCP_TOKEN (bearer token obtained through your
OAuth flow; optional for local Toolbox). Never logged.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from lookml_agentops.config import LkagentConfig, MCPRunnerConfig
from lookml_agentops.diagnose.models import AgentAnswer, RunnerMeta, TestCase
from lookml_agentops.diagnose.runners.answer_parse import parse_tool_payload
from lookml_agentops.diagnose.runners.base import AgentContext, Runner
from lookml_agentops.diagnose.runners.ca import RunnerConfigError


class MCPRunner(Runner):
    def __init__(self, conf: MCPRunnerConfig, *, url: str, token: str | None = None) -> None:
        if not conf.tool:
            raise RunnerConfigError("set diagnose.mcp.tool to the MCP tool that answers questions")
        self.conf = conf
        self.url = url
        self._token = token
        self._server_version = "unknown"

    @classmethod
    def from_config(cls, cfg: LkagentConfig) -> MCPRunner:
        url = os.environ.get("LKAGENT_MCP_URL")
        if not url:
            raise RunnerConfigError("MCP runner needs environment variable LKAGENT_MCP_URL")
        return cls(cfg.diagnose.mcp, url=url, token=os.environ.get("LKAGENT_MCP_TOKEN"))

    @property
    def meta(self) -> RunnerMeta:
        return RunnerMeta(
            runner="mcp",
            runner_version=f"mcp/{self._server_version}",
            structured=True,
            extra={"tool": self.conf.tool},
        )

    def arguments(self, question: str, agent: str) -> dict[str, Any]:
        return {
            self.conf.question_arg: question,
            **self.conf.extra_args,
            **self.conf.agent_args.get(agent, {}),
        }

    async def _call(self, question: str, agent: str) -> Any:
        try:
            import httpx2
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:  # pragma: no cover - optional extra
            raise RunnerConfigError(
                "install the [mcp] extra: pip install lookml-agentops[mcp]"
            ) from exc
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        async with (
            httpx2.AsyncClient(headers=headers, timeout=self.conf.timeout_seconds) as client,
            streamable_http_client(self.url, http_client=client) as (read, write),
            ClientSession(read, write) as session,
        ):
            init = await session.initialize()
            info = getattr(init, "server_info", None) or getattr(init, "serverInfo", None)
            if info is not None:
                self._server_version = (
                    f"{getattr(info, 'name', '?')}-{getattr(info, 'version', '?')}"
                )
            return await session.call_tool(self.conf.tool, self.arguments(question, agent))

    def answer(self, test: TestCase, ctx: AgentContext) -> AgentAnswer:
        try:
            result = asyncio.run(self._call(test.question, ctx.agent_id))
        except RunnerConfigError:
            raise
        except Exception as exc:  # never include headers/tokens in errors
            return AgentAnswer(meta=self.meta, error=f"MCP call failed: {type(exc).__name__}")
        return self.parse_result(result)

    def parse_result(self, result: Any) -> AgentAnswer:
        if getattr(result, "is_error", False):
            return AgentAnswer(meta=self.meta, error="MCP tool returned an error")
        payload: Any = getattr(result, "structured_content", None)
        if payload is None:
            texts = [getattr(c, "text", "") for c in getattr(result, "content", []) or []]
            joined = "\n".join(t for t in texts if t)
            try:
                payload = json.loads(joined)
            except ValueError:
                return AgentAnswer(
                    meta=self.meta.model_copy(update={"structured": False}), rows=None
                )
        return parse_tool_payload(payload, self.meta)
