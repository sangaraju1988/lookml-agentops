# `agent_instructions.v1`

lookml-agentops compiles LookML and the glossary into its **own neutral schema**, then exports
vendor formats from it. The JSON Schema is in
[`agent_instructions.v1.schema.json`](agent_instructions.v1.schema.json). A test keeps it in sync
with the pydantic models.

```
AgentInstructions
├── schema_version: "agent_instructions.v1"
├── agent_id                 # the spoke project
├── lookml_model             # model file stem
├── explores[]               # explore, label, description, default_time_field, fiscal_alias, extends_chain
├── layers[]                 # ordered: hub layer, then spoke layer
│   ├── layer_id / project / role
│   ├── rules[]              # rule_id, kind, text, provenance, test_ids, subjects, assertion, certified, data
│   ├── glossary[]
│   ├── field_guidance[]
│   ├── example_questions[]
│   └── content_hash
└── content_hash
```

## Rule kinds and sources

| kind | generated from | rule_id pattern | adherence test |
|---|---|---|---|
| `metric_definition` | certified or `ai_exposed` measures | `metric.<view>.<field>` | "What is the \<label\>?" must use the field |
| `vocabulary` | approved glossary terms + synonyms | `vocab.<term_id>` | one question per synonym, which must use the linked field |
| `default_filter` | explore `always_filter` | `default_filter.<explore>.<field>` | answer must carry the filter |
| `exclusion` | explore `sql_always_where` | `exclusion.<explore>` | generated SQL must contain the guard columns |
| `time_convention` | `fiscal_calendar` view, `ai_default_time` fields | `time.fiscal.<view>`, `time.default.<explore>` | "last quarter" / "last year" / "last month" resolve to completed periods |
| `pii_guardrail` | `pii`-tagged fields | `pii.<view>.<field>` | request must be refused or answered without PII |

## Layering rules

* A rule belongs to the layer of the project that **originated** its source. For example, a hub
  measure refined only in wording by a spoke stays a hub rule. The new wording shows up in the
  spoke layer's `field_guidance`.
* Hub-layer rules refer to hub explore names (`orders_base`), so the hub layer hash doesn't change
  when a spoke edits its own explores. Exporters map base explores to the spoke explores that
  extend them.
* Every rule makes a machine-checkable claim (`subjects` + `assertion`). If a spoke rule makes a
  different claim on a subject held by a **certified** hub rule, compile fails. Two different claims
  within one layer also fail (ambiguity). A spoke claim identical to a hub claim is dropped as a
  duplicate.

## Determinism

Output is pure templating over sorted metadata. JSON is written with sorted keys, and hashes cover
canonical JSON. Timestamps never appear in artifacts. `lkagent compile --check` fails CI when the
committed `build/` differs from a fresh compile.

## `--polish`

This is an optional interface (`lookml_agentops.compile.polish.Polisher`). A polisher may only
rewrite `text`. `apply_polish` rejects any change to rule IDs, kinds, provenance, subjects,
assertions, or test IDs. The unpolished JSON stays the source of truth. No LLM provider ships in
this release.
