"""Runner interface."""

from __future__ import annotations

import abc
import datetime as dt
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lookml_agentops.compile.schema import AgentInstructions
from lookml_agentops.lookml.resolve import EffectiveModel
from lookml_agentops.verify.models import AgentAnswer, RunnerMeta, TestCase

if TYPE_CHECKING:
    import duckdb


@dataclass
class SpokeContext:
    spoke: str
    instructions: AgentInstructions
    model: EffectiveModel
    as_of: dt.date
    con: duckdb.DuckDBPyConnection | None = None  # local warehouse (mock runner only)


class Runner(abc.ABC):
    """Asks one agent one question. Implementations must never log credentials or row data."""

    @property
    @abc.abstractmethod
    def meta(self) -> RunnerMeta: ...

    @abc.abstractmethod
    def answer(self, test: TestCase, ctx: SpokeContext) -> AgentAnswer: ...

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release resources (connections, sessions)."""
