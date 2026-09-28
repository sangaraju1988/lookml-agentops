# Running against your own Looker

This guide takes you from the offline demo to your own hub-and-spoke projects and a real agent.

## 1. Point lkagent at your projects

```yaml
# lkagent.yaml
version: 1
name: acme
as_of: 2026-01-20              # pin relative dates so golden answers are stable
projects:
  core_hub:     {path: ../core_hub,     role: hub,   owner: central data team}
  finance:      {path: ../finance,      role: spoke, owner: finance analytics}
catalog: {adapter: yaml, path: glossary.yaml}
golden: [golden/finance.yaml]
compile: {out_dir: build}
verify:
  runner: ca
  history: .lkagent/history.duckdb
  ca:
    location: global
    context_version: PUBLISHED
    agents:
      finance: finance-agent            # data agent id (or full resource name)
```

Each project path can be a separate git checkout. Spokes must declare the hub in
`manifest.lkml` (`local_dependency` or `remote_dependency`). A `remote_dependency` must be checked
out locally, and `projects.<hub>.path` must point at that checkout.

## 2. Tag your LookML

Follow [governance-tags.md](governance-tags.md): mark `certified` measures, `ai_exposed` fields
with `glossary:<term>`, `pii` / `pii:<kind>`, `ai_default_time` on each explore's main date, and
the fiscal calendar view. Then run `lkagent lint` until it's clean, or add exemptions with reasons.

## 3. Glossary

Export your business glossary to YAML (`id, name, definition, synonyms, owner, sensitivity,
linked_fields, status`). `linked_fields` use `<project>.<view>.<field>`, where `<project>` is the
project that **defines** the field. The Dataplex adapter is a stub until its API is verified.

## 4. Golden questions and ground truth

Write each golden test's ground truth as **warehouse SQL on raw tables**, independent of LookML,
using `$as_of` for relative dates. For a real warehouse, run the ground-truth SQL against a
snapshot or pinned partition. Keep the result aggregated. lkagent stores only fingerprints and
row counts, never rows.

## 5. Compile and deploy instructions

`lkagent compile` writes `build/<spoke>/ca_agent.json`, a `DataAgent` body using documented
fields only. Replace `${LOOKER_INSTANCE_URI}` at deploy time and create or update the agent with
your usual tooling. Review the `TODO(verify-api)` items in `compile/exporters/ca_exporter.py`
first.

## 6. Run a real runner

Credentials are read **only** from environment variables and are never logged or written:

| Runner | Environment |
|---|---|
| `ca` | `LKAGENT_CA_ACCESS_TOKEN` (e.g. `$(gcloud auth print-access-token)`), `GOOGLE_CLOUD_PROJECT`, optional `LOOKER_CLIENT_ID` / `LOOKER_CLIENT_SECRET` |
| `mcp` | `LKAGENT_MCP_URL` (e.g. `https://<instance>/mcp` or a local MCP Toolbox), optional `LKAGENT_MCP_TOKEN` |

```bash
pip install 'lookml-agentops[ca]'      # or [mcp]
export LKAGENT_CA_ACCESS_TOKEN=$(gcloud auth print-access-token)
export GOOGLE_CLOUD_PROJECT=my-project
lkagent verify --runner ca
```

If the runner's responses don't expose the generated Looker query (explore, fields, filters),
tests are compared on **results only**, and the report says so. For MCP, set `verify.mcp.tool`
and `question_arg` to the tool your server exposes. We couldn't confirm the tool names in the
docs; see [api-verification.md](api-verification.md).

## 7. CI

Copy `.github/workflows/pr.yml` into each LookML repo, pointing `LKAGENT_CONFIG` at your config.
For hub PRs, run `lkagent verify --hub-pr $BRANCH` from a checkout that contains every spoke, so
all spokes are tested against the proposed hub.
