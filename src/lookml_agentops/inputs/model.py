"""Tracked-input model.

Every run records a fingerprint of every input that can change an answer. Inputs have a stable
``input_id`` (``<kind-prefix>:<name>``), a kind, a version and an owner.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

InputKind = Literal[
    "lookml_project", "agent_spec", "catalog", "test_suite", "runner", "data", "config"
]

PREFIX: dict[InputKind, str] = {
    "lookml_project": "lookml",
    "agent_spec": "agent",
    "catalog": "catalog",
    "test_suite": "suite",
    "runner": "runner",
    "data": "data",
    "config": "config",
}
KIND_OF_PREFIX = {v: k for k, v in PREFIX.items()}


def input_id(kind: InputKind, name: str) -> str:
    return f"{PREFIX[kind]}:{name}"


def kind_of(iid: str) -> InputKind:
    prefix = iid.split(":", 1)[0]
    if prefix not in KIND_OF_PREFIX:
        raise ValueError(f"unknown input id prefix in {iid!r}")
    return KIND_OF_PREFIX[prefix]


class TrackedInput(BaseModel):
    input_id: str
    kind: InputKind
    version: str
    owner: str = "unassigned"
    path: str | None = None  # file or directory, relative to the config root
    meta: dict[str, Any] = Field(default_factory=dict)
