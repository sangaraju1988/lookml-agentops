# R0: Audit and change plan (generalization to any topology)

## 1. Status of the original milestones

| Milestone | Status | Evidence |
|---|---|---|
| M0 plan | done | written plan in the original session |
| M1 scaffold + seed | done | `8f7e357`: deterministic seed, stable hash test |
| M2 LookML + resolver | done | `07de6af`: provenance tests, import graph |
| M3 catalog + lint | done | `2700b34`: 19 rules, broken fixture snapshot |
| M4 compile | done | `e9be1c3`: zero-diff recompile, contradiction error |
| M5 golden + mock + verify | done | `0c08177`: v1 100%, documented v2/v3 failures |
| M6 attribute + report + demo | done | `62ca03c`: demo attributes vendor/hub/spoke |
| M7 CI + runners + docs | done, workflows not executed on GitHub (no remote) | `f9423b7`: fresh clone → `uv sync` → `lkagent demo` offline on 3.11 and 3.12 |

## 2. hub/spoke references

"hub"/"spoke" appear as concepts or identifiers in **every layer**:

- **config**: `ProjectConfig.role: hub|spoke`, `LkagentConfig.hub`, `.spokes`
- **compile**: `generate.py` (`hub` layer vs `spoke` layer, `layer_of`, contradiction check against the hub's certified rules), `schema.py` (`Layer.role`), `testgen.py`, `build.py`, `ca_exporter.py`
- **verify**: `run.py`, `runners/*` (`SpokeContext`, `ctx.spoke`), `models.py` (`TestCase.spoke`), `comparator.py` / `history.py` / `summary.py` (`spoke` columns), `loader.py`
- **attribute**: `attribute.py` (`Cause = vendor|hub|spoke|unknown`)
- **report**: `data.py`, templates, `svg.py` (per-spoke series)
- **lint**: `rules.py` (LKA007 compares every project against `ctx.hub`), `engine.py` (`ctx.hub`)
- **cli / demo**: `--hub-pr`, `--spoke-pr`, and the demo's hub/spoke scenarios
- **examples**: `lookml/core_hub`, `finance_spoke`, `logistics_spoke`, `build/*_spoke`, golden files (`spoke:`), glossary links (`core_hub.*`), lkagent.yaml
- **tests**: `fixtures/broken_hub` (`core_hub`, `bad_spoke`), and every test that passes spoke names
- **docs**: README, architecture, agent-instructions-v1 (+ JSON Schema), governance-tags, lint-rules, own-looker, expected-drift, TRAPS, CHANGELOG, SECURITY, workflows

## 3. Old → new mapping

| Old | New |
|---|---|
| `config.py` v1 (`projects.<p>.role`) | v2: `projects` (no role), `agents.paths`, `catalogs`, `suites`, `owners`, `diagnose.*` |
| `ProjectRevision` (tree hash) | `inputs/`: `TrackedInput(input_id, kind, version, owner)` plus element snapshots |
| — | `inputs/owners.py`: `owners.yaml`, most-specific match wins |
| `lookml/resolve.py` | kept. Import-DAG cycle detection added, and any project can be resolved on its own. |
| `lookml/graph.py` | kept, now topology-neutral (roots = projects nobody imports) |
| `compile/` (LookML-derived hub/spoke layers) | `generate/`: `.agent.md` → `agent_spec.v1` → exporters. LookML/catalog context is optional `derive`. |
| `compile/schema.py` `agent_instructions.v1` | `spec/model.py` `agent_spec.v1` |
| `compile/exporters/ca_exporter.py` (camelCase DataAgent body) | `generate/exporters/ca_api.py` (authored context, snake_case per the authored-context doc), `looker_ui.py`, `json.py` |
| hub-certified contradiction check | generic `locked: true` rules across any `extends` chain |
| `lint/` | kept. Rules made topology-neutral (LKA007 = a project redefining a certified measure it imports). New `LKS0xx` spec rules. |
| `verify/` | `diagnose/`: run, comparator, history v2, runners, deps, modeldiff |
| `verify/runners/mock.py` profiles v1/v2/v3 | external scenarios: `baseline`, `fuzzy_values`, `instruction_override`, `explore_selection_shift`. The mock consumes `agent_spec.v1`. |
| `attribute/` (vendor/hub/spoke) | `diagnose/why.py`: dependency-scoped verdicts `input` / `data` / `external` / `multiple`, with owners and confidence |
| `verify --hub-pr/--spoke-pr` | `diagnose impact --base REF --head REF [--run]` |
| — | `diagnose bundle`: vendor-case evidence zip |
| `lkagent lint/compile/verify/attribute/report` | `lkagent generate {new,lint,compile,resolve-golden,deploy,rollback}` and `lkagent diagnose {run,why,impact,bundle,report}` |
| `examples/harborline/lookml/{core_hub,finance_spoke,logistics_spoke}` | `projects/{core_project,finance_project,logistics_project,procurement_project}` |
| `golden/*.yaml` (`spoke:`) | `suites/*.yaml` (`agent:`); `input_id suite:<name>` |
| history schema v1 | v2 (inputs, elements, deps, baselines). A v1 file is moved aside to `history.v1.bak` automatically. |

## 4. Decisions on points the brief leaves open

1. **Duplicate IDs vs overrides.** Duplicate rule IDs across an extends chain are always an error (LKS007). A descendant "overrides" an ancestor rule by making a conflicting *claim* under its own rule ID. For unlocked ancestor rules the descendant wins. Against a `locked` ancestor rule it's a compile error (LKS008).
2. **Claims.** Rules stay free text for authors. `spec/interpret.py` extracts machine-checkable claims from common phrasings:
   - `"X" means <field or metric>` → vocabulary;
   - "last quarter … completed fiscal quarter" → time convention;
   - guardrail PII kinds.

   Claims drive generated tests, lock checks, the mock agent, and dependency tracing. A rule without a recognizable claim still compiles and gets a *presence* test, which checks that the rule is in the agent's context.
3. **Data verdicts need a baseline.** Each suite test's ground-truth result hash is baselined on first sight. If it later changes and the answer matches the new truth, the status is **drift**, the verdict is `data`, and CI isn't gated on it. `diagnose run --rebaseline` accepts the new truth.
4. **Precise impact.** Each dependency records which *facet* of an element matters:
   - **semantic** (sql, type, filters, joins, guards) matters only to tests that check results;
   - **surface** (label, description, tags, hidden) matters to every test, because it changes what an agent picks.

   A SQL-only change to `net_revenue` is therefore predicted to break exactly the result-checked tests that use it, directly or through `${}` references.
5. **Golden-query Explore URLs** resolve through the Looker API `GET /queries/slug/{slug}` (confirmed in the authored-context doc). The cache lives in `build/golden_cache.json`. The Looker `Query` field names used in the mapping (`model`, `view`, `fields`, `filters`, `sorts`, `limit`, `pivots`) are marked `TODO(verify-api)`.
6. **CA export uses snake_case authored-context names**, as in the authored-context doc: `system_instruction` as a YAML string with `system_instruction`, `glossaries`, `additional_descriptions`, plus `looker_golden_queries` and `datasource_references`.

## 5. Open questions

1. **R7 publish/rollback.** The DataAgent resource has `stagingContext`, `publishedContext` and output-only `lastPublishedContext`. The documented methods are `create/createSync/patch/updateSync/get/list/delete/deleteSync` and IAM only, with **no publish method**. Updating staging (`updateSync` + `updateMask`) and chatting against it (`dataAgentContext.contextVersion: STAGING`) are documented. Whether "publish" means writing `published_context` with `updateSync` isn't stated. I'm treating this as unclear and asking before implementing publish/rollback (see R7).
   **Resolved (option 1, manual):** `generate deploy` stages (preserving `published_context`),
   validates against STAGING, applies the pass-rate gate and records the result. Publishing and
   rollback are done by a person in the Looker/CA UI; `generate rollback` prints the steps and the
   recorded deployment history.
2. **Looker UI instruction format.** The UI's context types differ from the API's. The `looker-ui` exporter lists what doesn't translate, based on the Looker data-agent docs.
