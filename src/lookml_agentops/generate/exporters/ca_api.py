"""Conversational Analytics API authored context for a Looker data agent.

Shapes follow the "authored context for Looker data sources" doc and the v1 REST reference:

* ``system_instruction`` — a YAML-formatted string. We use the doc's template keys:
  ``system_instruction`` (role/persona), ``glossaries`` and ``additional_descriptions``.
* ``looker_golden_queries[]`` — ``{natural_language_questions[], looker_query{model, explore,
  fields[], filters[{field, value}], sorts[], limit}}``; Looker query representation only
  (never SQL or Explore URLs).
* ``datasource_references.looker.explore_references[{looker_instance_uri, lookml_model,
  explore}]`` — at most five explores.

The object is the value of ``data_analytics_agent.staging_context`` / ``published_context``.
Field names are snake_case as in the authored-context doc (proto JSON also accepts camelCase).
TODO(verify-api): the exact nesting of ``glossaries`` entries inside the system_instruction
YAML follows the doc's template; re-check it against the current doc before deploying.
"""

from __future__ import annotations

from typing import Any

import yaml

from lookml_agentops._util.hashing import canonical_json
from lookml_agentops.spec.model import AgentSpec

LOOKER_INSTANCE_URI_PLACEHOLDER = "${LOOKER_INSTANCE_URI}"


def _yaml(obj: Any) -> str:
    return yaml.safe_dump(
        obj, sort_keys=False, allow_unicode=True, width=100, default_flow_style=False
    )


def system_instruction(spec: AgentSpec) -> str:
    persona = " ".join(x.text for x in (spec.role, spec.audience) if x is not None)
    glossaries: list[dict[str, Any]] = []
    for v in spec.vocabulary:
        glossaries.append(
            {
                "glossary": [
                    {"term": v.phrases[0]},
                    {"description": f"Use {v.field or v.target}."},
                    {"synonyms": v.phrases[1:]},
                ]
            }
        )
    if spec.derived:
        for t in spec.derived.glossary:
            glossaries.append(
                {
                    "glossary": [
                        {"term": t.term},
                        {
                            "description": f"{t.description} (derived from {t.source}; field {t.field})"
                        },
                        {"synonyms": t.synonyms},
                    ]
                }
            )
    additional = [{"text": f"[{r.rule_id}] {r.text}"} for r in spec.active_rules()]
    additional += [{"text": f"[{g.guardrail_id}] Guardrail: {g.text}"} for g in spec.guardrails]
    if spec.derived:
        additional += [
            {"text": f"[derived] {d.field}: {d.text}"} for d in spec.derived.descriptions
        ]
    doc: dict[str, Any] = {"system_instruction": persona or spec.description}
    if glossaries:
        doc["glossaries"] = glossaries
    if additional:
        doc["additional_descriptions"] = additional
    return _yaml(doc)


def export_ca_context(spec: AgentSpec) -> tuple[str, list[str]]:
    """Return (JSON authored context, warnings)."""
    warnings: list[str] = []
    golden = []
    for q in spec.golden_queries:
        if q.looker_query is None:
            warnings.append(
                f"{spec.agent_id}: golden query {q.golden_id} has no resolved looker_query; skipped"
            )
            continue
        lq = q.looker_query
        query: dict[str, Any] = {"model": lq.model, "explore": lq.explore, "fields": lq.fields}
        if lq.filters:
            query["filters"] = [{"field": f.field, "value": f.value} for f in lq.filters]
        if lq.sorts:
            query["sorts"] = lq.sorts
        if lq.limit is not None:
            query["limit"] = lq.limit
        golden.append({"natural_language_questions": q.questions, "looker_query": query})
    context = {
        "system_instruction": system_instruction(spec),
        "looker_golden_queries": golden,
        "datasource_references": {
            "looker": {
                "explore_references": [
                    {
                        "looker_instance_uri": LOOKER_INSTANCE_URI_PLACEHOLDER,
                        "lookml_model": e.model,
                        "explore": e.explore,
                    }
                    for e in spec.explores
                ]
            }
        },
    }
    return canonical_json(context), warnings
