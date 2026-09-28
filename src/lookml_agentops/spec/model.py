"""``agent_spec.v1``: the neutral, compiled form of an agent spec.

Exporters (JSON, CA API authored context, Looker UI text) are produced from this schema; the
core never depends on a vendor format. Every rule, vocabulary entry, guardrail and golden query
keeps the file and line it came from.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lookml_agentops._util.hashing import sha256_obj

SCHEMA_VERSION = "agent_spec.v1"
ClaimKind = Literal["vocabulary", "time_period", "pii"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRef(_M):
    file: str  # relative to the config root
    line: int

    def __str__(self) -> str:
        return f"{self.file}:{self.line}"


class Claim(_M):
    """A machine-checkable reading of a rule, e.g. vocabulary 'revenue' -> 'net revenue'."""

    kind: ClaimKind
    subject: str  # normalized phrase, e.g. "revenue", "last quarter", "email"
    value: str  # target text as written, e.g. "net revenue", "last_completed_fiscal_quarter"
    field: str | None = None  # resolved alias.field (filled when bound to a model)
    explore: str | None = None


class Rule(_M):
    rule_id: str
    text: str
    locked: bool = False
    origin_agent: str
    source: SourceRef
    claims: list[Claim] = Field(default_factory=list)
    overridden_by: str | None = None  # rule id of a descendant rule that overrides a claim


class VocabEntry(_M):
    vocab_id: str
    phrases: list[str]
    target: str  # as written: explore.field or view.field
    field: str | None = None  # resolved alias.field
    explore: str | None = None
    origin_agent: str
    source: SourceRef


class Guardrail(_M):
    guardrail_id: str
    text: str
    origin_agent: str
    source: SourceRef
    pii_kinds: list[str] = Field(default_factory=list)
    covers: list[str] = Field(default_factory=list)  # field ids it protects (bound to a model)


class LookerFilter(_M):
    field: str
    value: str


class LookerQuery(_M):
    """Looker query representation used by CA golden queries."""

    model: str
    explore: str
    fields: list[str]
    filters: list[LookerFilter] = Field(default_factory=list)
    sorts: list[str] = Field(default_factory=list)
    limit: str | None = None
    pivots: list[str] = Field(default_factory=list)  # CA never generates pivots; lint rejects


class GoldenQuery(_M):
    golden_id: str
    questions: list[str]
    looker_query: LookerQuery | None = None
    explore_url: str | None = None
    resolved_from: Literal["inline", "url-cache", "unresolved"] = "inline"
    origin_agent: str
    source: SourceRef


class ExploreBinding(_M):
    project: str
    explore: str
    model: str | None = None
    label: str | None = None
    description: str | None = None
    default_time_field: str | None = None
    fiscal_alias: str | None = None


class DerivedTerm(_M):
    term: str
    description: str
    synonyms: list[str]
    field: str | None = None
    source: str  # e.g. "catalog:glossary#net_revenue"


class DerivedDescription(_M):
    field: str
    text: str
    source: str  # e.g. "lookml:core_project/views/orders.view.lkml:70"


class DerivedContext(_M):
    glossary: list[DerivedTerm] = Field(default_factory=list)
    descriptions: list[DerivedDescription] = Field(default_factory=list)


class ChainEntry(_M):
    agent_id: str
    file: str


class TextBlock(_M):
    text: str
    source: SourceRef


class AgentSpec(_M):
    schema_version: str = SCHEMA_VERSION
    agent_id: str
    description: str
    source: SourceRef
    extends_chain: list[ChainEntry] = Field(default_factory=list)  # ancestors, resolution order
    explores: list[ExploreBinding] = Field(default_factory=list)
    derive: dict[str, bool] = Field(default_factory=dict)
    role: TextBlock | None = None
    audience: TextBlock | None = None
    rules: list[Rule] = Field(default_factory=list)
    vocabulary: list[VocabEntry] = Field(default_factory=list)
    guardrails: list[Guardrail] = Field(default_factory=list)
    golden_queries: list[GoldenQuery] = Field(default_factory=list)
    derived: DerivedContext | None = None
    content_hash: str = ""

    def compute_hash(self) -> str:
        return sha256_obj(self.model_dump(mode="json", exclude={"content_hash"}))

    def finalize(self) -> AgentSpec:
        self.content_hash = self.compute_hash()
        return self

    @property
    def is_abstract(self) -> bool:
        """A spec with no explores is a base spec: validated and inherited, never deployed."""
        return not self.explores

    def active_rules(self) -> list[Rule]:
        return [r for r in self.rules if r.overridden_by is None]

    def element_hashes(self) -> dict[str, str]:
        """Hash per rule/vocab/guardrail/golden query, for dependency tracing."""
        out: dict[str, str] = {}
        for r in self.rules:
            out[f"rule:{r.rule_id}"] = sha256_obj([r.text, r.locked, r.overridden_by])[:16]
        for v in self.vocabulary:
            out[f"vocab:{v.vocab_id}"] = sha256_obj([v.phrases, v.target])[:16]
        for g in self.guardrails:
            out[f"guardrail:{g.guardrail_id}"] = sha256_obj(g.text)[:16]
        for q in self.golden_queries:
            out[f"golden:{q.golden_id}"] = sha256_obj(
                [
                    q.questions,
                    q.looker_query.model_dump(mode="json") if q.looker_query else q.explore_url,
                ]
            )[:16]
        return out

    def element_sources(self) -> dict[str, SourceRef]:
        out: dict[str, SourceRef] = {}
        for r in self.rules:
            out[f"rule:{r.rule_id}"] = r.source
        for v in self.vocabulary:
            out[f"vocab:{v.vocab_id}"] = v.source
        for g in self.guardrails:
            out[f"guardrail:{g.guardrail_id}"] = g.source
        for q in self.golden_queries:
            out[f"golden:{q.golden_id}"] = q.source
        return out


def dump_spec(spec: AgentSpec) -> dict[str, Any]:
    return spec.model_dump(mode="json")
