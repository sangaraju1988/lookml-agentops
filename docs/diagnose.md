# diagnose: why did an answer change?

## What a run records

`lkagent diagnose run` answers every suite test and generated test, then stores:

- every **tracked input** with its version and owner;
- **element snapshots**: facet versions plus each parameter's value and file:line;
- per test: status, answer *structure* (explore, fields, filters, resolved values, row count,
  result hash; never rows), ground-truth fingerprint, and its **dependency records**;
- **baselines**: the accepted ground-truth fingerprint per test. Baselines are set on first sight
  and changed only with `--rebaseline`.

`--explain-deps TEST` prints one test's dependency set with owners and file:line.

## Dependency tracing

| Role | Rank | What |
|---|---:|---|
| exercised | 0 | Spec elements the test targets or the answer used (rules, vocabulary, guardrails, golden queries) |
| expected / answer | 0 | Fields in the expected query and in the actual answer |
| via / guard / explore | 1 | Fields those reference through `${…}`, fields in the explore's guards, the explore itself |
| test / data | 1 | The test definition and its ground-truth result |
| term | 2 | Catalog terms linked to those fields |
| context | 3 | The agent's whole-spec context |
| runner | 4 | The runner |

Tests that check results depend on the `semantic` and `surface` facets. Structure-only tests
depend only on `surface`. That's what lets `impact` predict exactly which tests a SQL-only change
breaks.

## Attribution (`diagnose why A B`)

For each test whose status changed, compare **only its dependencies** between runs A and B:

1. **One tracked input changed** (LookML project, spec, catalog, suite) → `input`, naming the
   element and parameter diff (e.g. `orders.net_revenue sql modified at
   core_project/views/orders.view.lkml:87`) and its owner. Confidence is `high` when one element
   changed, `medium` when several changed in one input.
2. **Several inputs changed** → `multiple`, ordered by closeness. A changed element the test
   directly exercises is named as the likely cause. Confidence `low`.
3. **Only the ground-truth result changed** → `data`. The expected answer moved (e.g.
   late-arriving refunds). The test status is `drift`, not `fail`, and CI isn't gated on it.
4. **Nothing tracked changed** → `external`: vendor or runtime behaviour. External verdicts are
   grouped by trap tag (e.g. "5 failures, all trap:dirty-categorical → likely filter-value
   resolution change") with the run dates.

Verdicts are grouped by owner in reports.

## Impact (`diagnose impact`)

`--base REF` (git worktree, monorepo) or `--base-config PATH` (any checkout) against the working
tree or `--head REF`. Both states are fingerprinted, and each head test's static dependency set is
intersected with the changed element facets. `--run` runs only the affected tests. It works for a
change in any project, including one that other projects import.

## Evidence bundle (`diagnose bundle A B`)

A deterministic zip for a vendor support case:
- `README.md` summary;
- `runs.json` with timestamps and runner metadata;
- `inputs.json` showing every team-owned input and whether it's unchanged;
- one file per affected test with the question, old and new answer structure, and before/after
  fingerprints of every dependency.

No row-level data is included.

## History migration

History schema v2 adds inputs, element snapshots, dependencies and baselines. Opening a v1 file
moves it to `history.v1.bak.duckdb` and starts a fresh v2 store (with a notice). v1 runs lack
dependency data, so they can't be attributed. Rerun `diagnose run` to rebuild history.

## Mock external scenarios

`baseline`, `fuzzy_values` (filter values fuzzy-matched), `instruction_override` (a vendor
instruction maps "revenue"/"sales" to gross revenue), `explore_selection_shift` (the least
specific explore wins).
