"""Test-case and answer models shared by compile (test generation) and verify."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Period = Literal[
    "last_completed_fiscal_quarter",
    "last_completed_fiscal_year",
    "last_completed_month",
]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FilterExpect(_M):
    """Expected filter. Either ``values`` (categorical) or a time window.

    Time windows are given relative (``period``), absolute (``fiscal_year`` / ``fiscal_quarter``)
    or explicit (``start``/``end``), and resolved to ``[start, end)`` against the pinned as_of.
    """

    field: str
    values: list[str] | None = None
    period: Period | None = None
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    start: dt.date | None = None
    end: dt.date | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> FilterExpect:
        kinds = [
            self.values is not None,
            self.period is not None,
            self.fiscal_year is not None,
            self.start is not None,
        ]
        if sum(kinds) != 1:
            raise ValueError(
                f"filter on {self.field}: give exactly one of values/period/fiscal_year/start"
            )
        return self


class Tolerance(_M):
    relative: float = 0.0
    absolute: float = 0.0


class Expectation(_M):
    explore: str | None = None
    fields_any_of: list[list[str]] | None = None  # answer must contain every field of one set
    filters: list[FilterExpect] = Field(default_factory=list)
    ground_truth_sql: str | None = None  # path relative to the golden file
    tolerance: Tolerance = Field(default_factory=Tolerance)
    refuse_or_exclude_tags: list[str] = Field(default_factory=list)  # e.g. ["pii"]
    sql_contains: list[str] = Field(default_factory=list)


class TestCase(_M):
    __test__ = False  # not a pytest class
    id: str
    question: str
    spoke: str
    expect: Expectation
    tags: list[str] = Field(default_factory=list)
    kind: Literal["golden", "adherence"] = "golden"
    rule_ids: list[str] = Field(default_factory=list)


class TestFile(_M):
    __test__ = False
    version: int = 1
    tests: list[TestCase]


class ResolvedFilter(_M):
    field: str
    raw: str
    values: list[str] = Field(default_factory=list)  # resolved categorical values
    start: dt.date | None = None
    end: dt.date | None = None


class RunnerMeta(_M):
    runner: str
    runner_version: str
    vendor_profile: str | None = None
    structured: bool = True  # False: runner cannot expose explore/fields/filters
    extra: dict[str, Any] = Field(default_factory=dict)


class AgentAnswer(_M):
    """Normalized answer from any runner. ``rows`` are aggregated results only."""

    explore: str | None = None
    fields: list[str] = Field(default_factory=list)
    filters: list[ResolvedFilter] = Field(default_factory=list)
    sql: str | None = None
    rows: list[list[Any]] | None = None
    refused: bool = False
    refusal_reason: str | None = None
    applied_rules: list[str] = Field(default_factory=list)
    meta: RunnerMeta
    error: str | None = None
