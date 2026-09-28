from __future__ import annotations

from lookml_agentops._util.hashing import canonical_json
from lookml_agentops.spec.model import AgentSpec


def export_json(spec: AgentSpec) -> str:
    """The neutral agent_spec.v1, with provenance for every element."""
    return canonical_json(spec.model_dump(mode="json"))
