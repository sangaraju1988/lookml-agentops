# Changelog

## 0.2.0 (unreleased): generalized to any topology

- **Breaking:** `lkagent.yaml` version 2. Projects have no roles. New sections: `agents`,
  `catalogs`, `suites`, `owners`, `diagnose`.
- **Breaking:** the CLI is split into two modes, `lkagent generate {new, lint, compile,
  resolve-golden}` and `lkagent diagnose {run, why, impact, bundle, report}`.
- Team-authored `*.agent.md` specs with generic `extends` and `locked` rules; neutral
  `agent_spec.v1`; exporters json / ca-api / looker-ui.
- Tracked inputs with owners (`owners.yaml`), dependency tracing, field-level model diffs,
  verdicts input / multiple / data / external, impact analysis, vendor evidence bundles.
- History schema v2 (v1 files are moved aside automatically).
- Example: four projects (including a standalone one), four agent specs, owners for six teams.

## 0.1.0

First MVP with a fixed hub-and-spoke layout (superseded by 0.2.0).
