"""Runner interface."""

from __future__ import annotations

import abc
import datetime as dt
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lookml_agentops.diagnose.models import AgentAnswer, RunnerMeta, TestCase
from lookml_agentops.generate.bind import Binding
from lookml_agentops.spec.model import AgentSpec

if TYPE_CHECKING:
    import duckdb


@dataclass
class AgentContext:
    agent_id: str
    spec: AgentSpec  # compiled agent_spec.v1 (bound)
    binding: Binding  # resolved explores + models
    as_of: dt.date
    con: duckdb.DuckDBPyConnection | None = None  # local warehouse (mock runner only)


class Runner(abc.ABC):
    """Asks one agent one question. Implementations must never log credentials or row data."""

    @property
    @abc.abstractmethod
    def meta(self) -> RunnerMeta: ...

    @abc.abstractmethod
    def answer(self, test: TestCase, ctx: AgentContext) -> AgentAnswer: ...

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release resources (connections, sessions)."""
