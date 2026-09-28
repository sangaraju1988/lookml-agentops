# Expected drift under the mock vendor profiles

The MockRunner simulates vendor behaviour changes. On the Harborline example (seed `20260120`,
`as_of` 2026-01-20), the profiles produce the following results. `tests/integration/test_verify.py`
enforces them, so any change to these lists is deliberate.

| Profile | What changes | Failing tests | Why |
|---|---|---|---|
| `v1` | baseline | none (100% pass) | — |
| `v2_fuzzy_values` | Filter values resolve by fuzzy matching against sampled values | `log-002`, `log-004`, `log-005`, `log-009`, `log-015` | All are `trap:dirty-categorical`. `"Shipped"` also matches `Shipped - Partial` (and vice versa). `Northstar Freight` and `North Star Freight LLC` merge. |
| `v3_instruction_override` | A vendor system instruction maps "revenue"/"sales" to gross revenue | `fin-002`, `fin-007`, `log-011`, `adh.vocab.net_revenue.1`, `adh.vocab.net_revenue.2` (both spokes) | All ask for bare "revenue"/"sales". The team rule `vocab.net_revenue` is overridden. |

Questions that name the metric explicitly ("net revenue", "net sales") are unaffected by v3.
Questions without quoted filter values are unaffected by v2. That's why `lkagent attribute`
groups vendor-attributed changes by trap tag: the tag pattern points to the vendor behaviour
that changed.
