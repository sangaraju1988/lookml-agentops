"""Optional prose polishing.

A polisher may rewrite rule *text* only. :func:`apply_polish` enforces that the rule set (IDs,
kinds, provenance, subjects, assertions, test IDs) is untouched; the unpolished instructions
remain the source of truth and are what gets hashed and tested.
"""

from __future__ import annotations

from typing import Protocol

from lookml_agentops.compile.schema import AgentInstructions


class PolishError(Exception):
    pass


class Polisher(Protocol):
    def polish(self, rule_id: str, text: str) -> str: ...


def _skeleton(instr: AgentInstructions) -> list[tuple[str, ...]]:
    return [
        (
            layer.layer_id,
            r.rule_id,
            r.kind,
            r.provenance.model_dump_json(),
            ",".join(r.subjects),
            r.assertion,
            ",".join(r.test_ids),
        )
        for layer in instr.layers
        for r in layer.rules
    ]


def apply_polish(instr: AgentInstructions, polisher: Polisher) -> AgentInstructions:
    out = instr.model_copy(deep=True)
    for layer in out.layers:
        for r in layer.rules:
            new = polisher.polish(r.rule_id, r.text)
            if not new.strip():
                raise PolishError(f"polisher returned empty text for {r.rule_id}")
            r.text = new
    if _skeleton(out) != _skeleton(instr):
        raise PolishError("polisher changed the rule set; only prose may change")
    return out
