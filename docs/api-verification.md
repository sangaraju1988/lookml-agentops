# Vendor API verification log

lookml-agentops never guesses vendor fields. Every field a real adapter uses is listed here with
its source. Anything unconfirmed is marked `TODO(verify-api)` in code.

_Last reviewed: 2026-09-27._

## Looker LookML semantics (used by the resolver)

| Fact | Status | Source |
|---|---|---|
| Refinements apply in include order. Within a file, later lines win. | ✅ confirmed | docs.cloud.google.com/looker/docs/lookml-refinements |
| Order: extends of the object → extends in refinements → object → refinements | ✅ confirmed | same |
| `extends` inside a refinement is appended | ✅ confirmed | same |
| `join`, `link`, `filters`, `action`, `aggregate_table`, `access_filter` are additive in refinements | ✅ confirmed (we implement `join`, `link`, `filters`, `action`) | same |
| Refining objects from imported projects is supported | ✅ confirmed | same |
| `local_dependency: { project: ... }`, `remote_dependency: name { url ref override_constant }` | ✅ confirmed | docs.cloud.google.com/looker/docs/importing-projects |
| Imported includes use `//project/path`. Model files can't be imported. | ✅ confirmed | same |
| `final: yes` on refinements | ⚠️ not enforced by our resolver | lookml-refinements |
| Explore `extends` inherits the parent's `view_name` | ⚠️ unverified, so our example explores set `view_name` explicitly | — |
| `fiscal_month_offset` fiscal-year naming | ⚠️ unverified, so we avoid it and use a fiscal calendar table | — |

## Conversational Analytics API (CA exporter, CARunner)

| Fact | Status | Source |
|---|---|---|
| Service `geminidataanalytics.googleapis.com`, versions `v1` and `v1beta` | ✅ confirmed | docs.cloud.google.com/gemini/docs/conversational-analytics-api/overview |
| Resources `dataAgents` (+ `:createSync`), `conversations`, `messages`, `locations/*:chat`, v1beta `conversations:queryData` | ✅ confirmed | same |
| `DataAgent{displayName, description, labels, dataAnalyticsAgent{stagingContext, publishedContext, lastPublishedContext}}` | ✅ confirmed | REST reference `projects.locations.dataAgents` |
| `Context{systemInstruction, datasourceReferences, options, exampleQueries, lookerGoldenQueries, glossaryTerms, schemaRelationships}` | ✅ confirmed | same |
| `DatasourceReferences.looker.exploreReferences[{lookerInstanceUri, lookmlModel, explore, schema}]` | ✅ confirmed | same |
| `GlossaryTerm{displayName, description, labels[]}` | ✅ field names confirmed. ⚠️ TODO(verify-api): whether `labels` means synonyms | same |
| `LookerGoldenQuery{naturalLanguageQuestions[], lookerQuery{model, explore, fields[], filters[{field,value}], sorts[], limit}}` | ✅ confirmed | same |
| Which context to populate on create (staging vs published) | ⚠️ TODO(verify-api) | — |
| `systemInstruction` recommended structure and size limits | ⚠️ TODO(verify-api) | — |
| Up to five explores per Looker data agent | ✅ stated in the overview. The exporter warns. | conversational-analytics-overview |
| `POST .../locations/*:chat` with `messages[{userMessage{text}}]` and `dataAgentContext{dataAgent, contextVersion}`. The response is a stream (JSON array) of `Message`. | ✅ confirmed | REST reference `projects.locations/chat`, `Message` |
| `systemMessage.{text{parts,textType}, data{query, generatedSql, result{schema, data}}, error{text}}` | ✅ confirmed | same |
| Where the generated Looker query lives in a data message (`data.generatedLookerQuery` vs `data.query.looker`) | ⚠️ TODO(verify-api): the reference pages disagree. CARunner accepts both and falls back to result-only when neither is present. | same |
| Placement of Looker OAuth credentials in the chat request | ⚠️ TODO(verify-api): we send top-level `credentials.oauth.secret{clientId, clientSecret}` | chat reference summary |
| A model/version identifier in responses (needed for positive vendor attribution) | ⚠️ not found. Vendor attribution works by elimination. | — |
| Resolved filter values exposed in the response | ⚠️ TODO(verify-api), assumed **no** | — |
| Auth for Looker datasources (credentials in request, OAuth, IAM roles) | ⚠️ TODO(verify-api) | authentication page not yet reviewed |

## Looker managed MCP server (MCPRunner)

| Fact | Status | Source |
|---|---|---|
| Endpoint `<LOOKER_INSTANCE_URL>/mcp`, HTTP transport | ✅ confirmed | docs.cloud.google.com/looker/docs/mcp |
| Auth: OAuth 2.1 with PKCE. Admins enable individual tools. | ✅ confirmed | same |
| Tool names and input schemas | ⚠️ TODO(verify-api): not listed on that page. MCPRunner calls the tool named in `verify.mcp.tool` with `{question_arg: question}` plus configured args. | — |
| Python MCP SDK 2.2: `streamable_http_client(url, http_client=httpx2.AsyncClient)` yields `(read, write)`; `ClientSession.call_tool(name, arguments)` → `CallToolResult{content, structured_content, is_error}` | ✅ verified against the installed SDK | `mcp` 2.2.0 |

## MCP Toolbox for Databases (Looker source)

| Fact | Status | Source |
|---|---|---|
| Tools include `looker-query`, `looker-query-sql` (returns generated SQL), `looker-get-explores`, `looker-get-dimensions`, `looker-get-measures`, `looker-conversational-analytics` | ✅ names confirmed | mcp-toolbox.dev/integrations/looker/ |
| Tool parameter names, source config keys, auth | ⚠️ TODO(verify-api) | — |

## Dataplex / knowledge catalog

| Fact | Status |
|---|---|
| Business glossary resources, term↔column links, export format | ⚠️ TODO(verify-api): `DataplexCatalogAdapter` is a stub |
