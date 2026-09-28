"""``agent_instructions.v1`` — lookml-agentops' own neutral schema.

Vendor formats (e.g. Conversational Analytics data agents) are produced by exporters from this
schema; the core never depends on a vendor format.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lookml_agentops._util.hashing import sha256_obj

SCHEMA_VERSION = "agent_instructions.v1"

RuleKind = Literal[
    "metric_definition",
    "default_filter",
    "exclusion",
    "time_convention",
    "pii_guardrail",
    "vocabulary",
]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Provenance(_M):
    project: str
    file: str
    line: int
    glossary_term_id: str | None = None


class InstructionRule(_M):
    rule_id: str
    kind: RuleKind
    text: str
    provenance: Provenance
    test_ids: list[str] = Field(default_factory=list)
    # machine-checkable claim: same subject + different assertion across layers = contradiction
    subjects: list[str] = Field(default_factory=list)
    assertion: str = ""
    certified: bool = False
    # structured payload for exporters / runners (field ids, filter values, ...)
    data: dict[str, Any] = Field(default_factory=dict)


class GlossaryEntry(_M):
    term_id: str
    name: str
    definition: str
    synonyms: list[str]
    fields: list[str]  # explore/alias.field usages in this agent


class FieldGuidance(_M):
    field: str  # view.field
    kind: str
    label: str
    description: str
    usages: list[str]  # explore/alias.field
    tags: list[str]


class ExampleQuestion(_M):
    question: str
    explore: str
    fields: list[str]
    filters: dict[str, str] = Field(default_factory=dict)


class Layer(_M):
    layer_id: str  # "hub:core_hub" / "spoke:finance_spoke"
    project: str
    role: Literal["hub", "spoke"]
    rules: list[InstructionRule] = Field(default_factory=list)
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    field_guidance: list[FieldGuidance] = Field(default_factory=list)
    example_questions: list[ExampleQuestion] = Field(default_factory=list)
    content_hash: str = ""

    def compute_hash(self) -> str:
        return sha256_obj(self.model_dump(mode="json", exclude={"content_hash"}))


class ExploreRef(_M):
    explore: str
    label: str
    description: str
    default_time_field: str | None = None
    fiscal_alias: str | None = None
    extends_chain: list[str] = Field(default_factory=list)


class AgentInstructions(_M):
    schema_version: str = SCHEMA_VERSION
    agent_id: str
    lookml_model: str
    explores: list[ExploreRef]
    layers: list[Layer]
    content_hash: str = ""

    def compute_hash(self) -> str:
        return sha256_obj(self.model_dump(mode="json", exclude={"content_hash"}))

    def all_rules(self) -> list[InstructionRule]:
        return [r for layer in self.layers for r in layer.rules]

    def layer_hashes(self) -> dict[str, str]:
        return {layer.layer_id: layer.content_hash for layer in self.layers}
