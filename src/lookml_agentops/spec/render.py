"""Render a :class:`ParsedSpec` back to canonical ``*.agent.md`` text (used by round-trip tests
and ``generate new``)."""

from __future__ import annotations

from typing import Any

import yaml

from lookml_agentops.spec.parse import ParsedSpec


def _dump(obj: Any) -> str:
    return yaml.safe_dump(
        obj, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000
    ).rstrip()


def render_spec(spec: ParsedSpec) -> str:
    f = spec.front
    front: dict[str, Any] = {"agent": f.agent, "description": f.description}
    if f.extends:
        front["extends"] = f.extends
    if f.explores:
        front["explores"] = f.explores
    derive = f.derive.model_dump()
    if any(derive.values()):
        front["derive"] = derive
    out = ["---", _dump(front), "---", ""]
    if spec.role:
        out += ["## Role", spec.role[0], ""]
    if spec.audience:
        out += ["## Audience", spec.audience[0], ""]
    if spec.rules:
        items = [
            {"id": r.rule_id, "text": r.text, **({"locked": True} if r.locked else {})}
            for r in spec.rules
        ]
        out += ["## Rules", _dump(items), ""]
    if spec.vocabulary:
        out.append("## Vocabulary")
        out += [
            f"- {', '.join(chr(34) + p + chr(34) for p in v.phrases)} → {v.target}"
            for v in spec.vocabulary
        ]
        out.append("")
    if spec.guardrails:
        g_items: list[Any] = [
            {"id": g.guardrail_id, "text": g.text} if g.guardrail_id else g.text
            for g in spec.guardrails
        ]
        out += ["## Guardrails", _dump(g_items), ""]
    if spec.golden:
        q_items = []
        for q in spec.golden:
            item: dict[str, Any] = {"id": q.golden_id, "questions": q.questions}
            if q.looker_query is not None:
                item["looker_query"] = q.looker_query
            else:
                item["explore_url"] = q.explore_url
            q_items.append(item)
        out += ["## Golden queries", _dump(q_items), ""]
    return "\n".join(out).rstrip() + "\n"
