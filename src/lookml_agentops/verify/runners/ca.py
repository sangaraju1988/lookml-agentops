"""Conversational Analytics runner (implemented in M7)."""

from __future__ import annotations

from lookml_agentops.verify.models import AgentAnswer, RunnerMeta, TestCase
from lookml_agentops.verify.runners.base import Runner, SpokeContext


class CARunner(Runner):
    @classmethod
    def from_env(cls) -> CARunner:
        raise NotImplementedError("CARunner is not implemented yet")

    @property
    def meta(self) -> RunnerMeta:
        return RunnerMeta(runner="ca", runner_version="unknown", structured=False)

    def answer(self, test: TestCase, ctx: SpokeContext) -> AgentAnswer:
        raise NotImplementedError
