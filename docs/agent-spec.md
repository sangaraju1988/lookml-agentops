# Agent spec reference (`*.agent.md`)

An agent spec is a Markdown file with YAML frontmatter, written like a skill file. Any team can
author one. Specs are discovered via `agents.paths` in `lkagent.yaml`, and each becomes the
tracked input `agent:<agent id>`.

## Frontmatter

| Key | Required | Meaning |
|---|---|---|
| `agent` | yes | Stable id (lowercase letters, digits, `-`, `_`) |
| `description` | yes | One line; becomes the agent description |
| `extends` | no | Ordered list of spec files (relative to this file). Any spec can extend any others; cycles are errors. |
| `explores` | no | `<project>::<explore>` entries (at most 5, the CA limit). A spec without explores is *abstract*: it's validated and inherited, but never compiled or deployed. |
| `derive` | no | `lookml_descriptions` and/or `catalog_glossary`: append derived context in a separate section. It never overrides authored rules. |

Unknown keys are errors.

## Sections

Sections are parsed by `##` heading. Unknown sections are errors.

| Section | Format |
|---|---|
| `## Role` | Free text (persona) |
| `## Audience` | Free text |
| `## Rules` | YAML list of `{id, text, locked?}`. IDs must be unique across the extends chain. |
| `## Vocabulary` | Lines like `- "phrase", "other phrase" → explore.field` (or `view.field`) |
| `## Guardrails` | YAML list of strings or `{id, text}`. Guardrail IDs default to a stable hash of the text. |
| `## Golden queries` | YAML list of `{id, questions, looker_query}` or `{id, questions, explore_url}` |

## Claims

Rules are free text. These phrasings are read as machine-checkable *claims*, which drive
generated tests, lock checks, the mock agent, and attribution:

- `"X" means Y` / `"X" and "Z" mean Y` / `"X" refers to Y`: a vocabulary claim. `Y` is resolved to
  a field by label, glossary term, or spec vocabulary.
- `"Last quarter" means the last completed fiscal quarter` (also last year / last month): a
  time-convention claim.
- In guardrails, naming email / phone / date of birth / address / contact name / SSN: a PII claim.
  It covers every `pii:<kind>` field of that kind in the agent's explores.

A rule without a recognizable claim is compiled as prose and gets a *presence* test, which checks
that it is in the agent's context.

## extends and locked

- Ancestors are resolved depth-first in listed order, each once. The descendant's Role/Audience
  override inherited ones.
- A descendant overrides an unlocked ancestor rule by making a conflicting claim under its **own**
  rule ID. Reusing an ID is an error (`LKS007`).
- A `locked: true` rule can't be overridden or contradicted, by a rule or by a vocabulary entry
  (`LKS008`). This replaces any built-in notion of a central project's certified definitions.

## Golden queries

Use `looker_query` (the Looker query representation CA expects: `model`, `explore`, `fields`,
`filters[{field, value}]`, `sorts`, `limit`) so compiling needs no Looker access. An `explore_url`
is resolved with `lkagent generate resolve-golden` (Looker API `GET /queries/slug/{slug}`) and
cached in `build/golden_cache.json`. Without a cached resolution it's a warning (`LKS012`) and it
isn't exported. Golden queries must not use pivots (`LKS005`) and must use the agent's own
explores (`LKS006`).

## Outputs

| File | Content |
|---|---|
| `agent_spec.json` | The neutral `agent_spec.v1`, with file:line for every element |
| `ca_context.json` | CA API authored context: `system_instruction` (YAML with `system_instruction`, `glossaries`, `additional_descriptions`), `looker_golden_queries`, `datasource_references`. It's the value of `staging_context` / `published_context`. |
| `looker_ui.md` | Text for the Looker UI editor, plus what doesn't translate: no glossary field, Explore URLs only for verified queries, one question per verified query, no lock enforcement |
| `tests.generated.yaml` | One test per rule claim / prose rule / vocabulary phrase / guardrail-covered field / golden query, each recording the spec element it exercises |
