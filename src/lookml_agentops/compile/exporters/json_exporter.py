from __future__ import annotations

from lookml_agentops._util.hashing import canonical_json
from lookml_agentops.compile.schema import AgentInstructions


def export_json(instr: AgentInstructions) -> str:
    return canonical_json(instr.model_dump(mode="json"))
