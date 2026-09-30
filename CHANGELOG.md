# Changelog

## Unreleased

- `lkagent init`: scaffold `lkagent.yaml`, `owners.yaml`, a glossary, and starter agent specs and
  suites from existing LookML projects (`--scan DIR` or `--project NAME=PATH`), then lint and
  compile the result.

## 0.2.0: generalized to any topology

- **Breaking:** `lkagent.yaml` version 2. Projects have no roles. New sections: `agents`,
  `catalogs`, `suites`, `owners`, `diagnose`.
- **Breaking:** the CLI is split into two modes, `lkagent generate {new, lint, compile,
  resolve-golden}` and `lkagent diagnose {run, why, impact, bundle, report}`.
- Team-authored `*.agent.md` specs with generic `extends` and `locked` rules; neutral
  `agent_spec.v1`; exporters json / ca-api / looker-ui.
- Tracked inputs with owners (`owners.yaml`), dependency tracing, field-level model diffs,
  verdicts input / multiple / data / external, impact analysis, vendor evidence bundles.
- History schema v2 (v1 files are moved aside automatically).
- `lkagent generate deploy` (staged: write staging, test against it, pass-rate gate). Publishing
  and rollback are manual in the Looker/CA UI; `generate rollback` prints the steps.
- Example: four projects (including a standalone one), four agent specs, owners for six teams.
- Ground truth on BigQuery (`diagnose.ground_truth.engine: bigquery`, `[bigquery]` extra) with a
  bytes-billed cap, query labels, `$param` substitution and a row limit that keeps ground truth
  aggregated.
- The Harborline example ships inside the package, so `pip install lookml-agentops && lkagent demo`
  works offline.
- Release workflow (GitHub Release and PyPI trusted publishing), community files, Dependabot.

## 0.1.0

First MVP with a fixed hub-and-spoke layout (superseded by 0.2.0).
