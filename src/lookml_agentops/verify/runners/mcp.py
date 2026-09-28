"""MCP runner (implemented in M7)."""

from __future__ import annotations

from lookml_agentops.verify.models import AgentAnswer, RunnerMeta, TestCase
from lookml_agentops.verify.runners.base import Runner, SpokeContext


class MCPRunner(Runner):
    @classmethod
    def from_env(cls) -> MCPRunner:
        raise NotImplementedError("MCPRunner is not implemented yet")

    @property
    def meta(self) -> RunnerMeta:
        return RunnerMeta(runner="mcp", runner_version="unknown", structured=False)

    def answer(self, test: TestCase, ctx: SpokeContext) -> AgentAnswer:
        raise NotImplementedError
