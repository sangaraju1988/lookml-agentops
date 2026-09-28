# Running against your own Looker

## 1. Declare your inputs

```yaml
# lkagent.yaml
version: 2
name: acme
as_of: 2026-01-20              # pin relative dates so golden answers are stable
projects:                      # any topology: standalone projects, imports, diamonds
  core_models: {path: ../core_models}
  finance_models: {path: ../finance_models}
agents: {paths: ["agents/**/*.agent.md"]}
catalogs: {glossary: {adapter: yaml, path: glossary.yaml}}
suites: {finance: suites/finance.yaml}
owners: owners.yaml
diagnose:
  runner: ca
  ca:
    location: global
    context_version: PUBLISHED
    agents: {finance-analyst: finance-agent}   # agent id -> CA data agent id or resource name
```

Each project path can be a separate git checkout. A project that imports another declares it in
`manifest.lkml` (`local_dependency`, or `remote_dependency` checked out locally).

## 2. Ownership

```yaml
# owners.yaml
owners:
  - match: "lookml:core_models/**"
    team: central-data-platform
  - match: "lookml:finance_models/views/revenue/**"   # path globs inside an input
    team: revenue-analytics
  - match: "agent:finance-*"
    team: finance-analytics
  - match: "runner:*"
    team: bi-platform
```

## 3. Write specs, lint, compile

Write `agents/*.agent.md` (see [agent-spec.md](agent-spec.md)), or start with
`lkagent generate new <id> --explore project::explore -d "..."`. Then run `lkagent generate lint`
and `lkagent generate compile`. Tag LookML per [governance-tags.md](governance-tags.md).

## 4. Golden suites and ground truth

Write each suite test's ground truth as **warehouse SQL on raw tables**, independent of LookML,
using `$as_of` for relative dates. Keep results aggregated. lkagent stores only fingerprints and
row counts.

## 5. Run a real runner

Credentials are read **only** from environment variables and are never logged or written:

| Runner | Environment |
|---|---|
| `ca` | `LKAGENT_CA_ACCESS_TOKEN` (e.g. `$(gcloud auth print-access-token)`), `GOOGLE_CLOUD_PROJECT`, optional `LOOKER_CLIENT_ID` / `LOOKER_CLIENT_SECRET` |
| `mcp` | `LKAGENT_MCP_URL`, optional `LKAGENT_MCP_TOKEN`; set `diagnose.mcp.tool` / `question_arg` |
| `resolve-golden` | `LOOKER_BASE_URL`, `LOOKER_CLIENT_ID`, `LOOKER_CLIENT_SECRET` |

```bash
pip install 'lookml-agentops[ca]'
export LKAGENT_CA_ACCESS_TOKEN=$(gcloud auth print-access-token) GOOGLE_CLOUD_PROJECT=my-project
lkagent diagnose run --runner ca
```

If the runner's responses don't expose the generated Looker query, tests are compared on
**results only**, and reports say so. See [api-verification.md](api-verification.md).

## 6. Staged deploy

```bash
export LOOKER_INSTANCE_URI=https://<instance>.looker.app
lkagent generate deploy finance-analyst
```

This writes `build/finance-analyst/ca_context.json` into the data agent's `staging_context`
(sending the current `published_context` back unchanged), runs the agent's tests against staging
(`contextVersion: STAGING`), and records the result in `.lkagent/deployments.json`. It reports
"ready to publish" only when the pass rate meets `diagnose.deploy.pass_threshold`.

**Publishing and rollback are manual by design.** lkagent never changes what live users see. After
a green staged run, a person publishes from the Looker / Conversational Analytics UI. To roll back,
re-stage the last good spec (`git checkout <commit> -- agents/`, then `lkagent generate deploy`)
and publish it, or put back the previous instructions in the UI. `lkagent generate rollback
<agent>` prints these steps plus the recorded deployment history.

## 7. CI for any project

Copy `.github/workflows/pr.yml` into each repo, pointing `LKAGENT_CONFIG` at your config. On a PR
to a project that others import, `lkagent diagnose impact --base origin/main --run` tests every
affected agent against the proposed change.
