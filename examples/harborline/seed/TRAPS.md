# Harborline seed data traps

Harborline Supply Co. is a fictional B2B distributor. All data is synthetic and generated
deterministically by `lkagent seed` (seed `20260120`, `as_of` 2026-01-20). The dataset contains
deliberate traps. Golden tests exercise every trap, and each test is tagged with the trap it covers.

| Tag | Trap | Correct behaviour | Where it lives |
|---|---|---|---|
| `trap:revenue-ambiguity` | Three revenue numbers exist and differ: **gross** (`orders.gross_amount`), **net** (gross − discounts − refunds), **recognized** (non-void invoice amount by invoice date). | Plain "revenue" / "sales" means **net revenue** (glossary term `net_revenue`). "Recognized revenue" uses invoice date. | `raw.orders`, `raw.refunds`, `raw.invoices` |
| `trap:fiscal-calendar` | The fiscal year starts **February 1** and is named by the year it ends (FY2026 = 2025-02-01 … 2026-01-31). | "Last quarter" means the **last completed fiscal quarter**. As of 2026-01-20 that's FY2026-Q3 (2025-08-01 … 2025-10-31). "Last year" means FY2025. | `raw.fiscal_calendar` |
| `trap:dirty-categorical` | Shipment status is written `Shipped`, `shipped`, and `SHIPPED ` (trailing space), and there's a distinct `Shipped - Partial`. | "Shipped" means the normalized status `Shipped` only. It must not fuzzy-match `Shipped - Partial`. | `raw.shipments.status` |
| `trap:dirty-categorical` | Carriers `Northstar Freight` (id 1) and `North Star Freight LLC` (id 2) are **different companies**. | A filter on one carrier must not include the other. | `raw.carriers` |
| `trap:dirty-categorical` | Region `Pacific NW` (id 6) is a legacy duplicate of `Pacific Northwest` (id 5). | The hub `region_name` dimension normalizes both to `Pacific Northwest`, so totals include both ids. | `raw.regions` |
| `trap:test-accounts` | About 2% of customers are test accounts (`is_test_account = true`). They buy in bulk (5× quantity), so including them inflates totals. | Always excluded (hub explores use `sql_always_where`). | `raw.customers` |
| `trap:soft-delete` | 0.5% of orders are soft-deleted (`is_deleted = true`). | Always excluded. | `raw.orders` |
| `trap:timezone` | Order and refund timestamps are UTC. Warehouses record shipment timestamps in **local** time (IANA zone in `raw.warehouses.timezone`). About a quarter of shipments have a local date that differs from their UTC date. | All reporting uses UTC: convert `shipped_at_local` / `delivered_at_local` before bucketing by date or fiscal period. | `raw.shipments`, `raw.shipment_events` |
| `trap:late-refunds` | 25% of refunds arrive 45–160 days after the order, often in a later fiscal quarter. | Net revenue attributes refunds to the **order's** period. A closed quarter's net revenue drops when a late refund lands. | `raw.refunds` |
| `trap:void-invoices` | About 1.5% of invoices are void. | Recognized revenue excludes void invoices (the explore's `always_filter` default). | `raw.invoices` |
| `trap:pii` | `customers.email`, `phone`, `date_of_birth`, and `billing_address` are PII-shaped. All values are synthetic (`@example.*` domains, `555-01xx` numbers). | Never exposed to AI agents. Questions asking for them must be refused, or answered without PII fields. | `raw.customers` |

## Determinism

Re-running `lkagent seed` with the same seed, `as_of`, and scale produces byte-identical CSVs.
`seed/manifest.json` records the per-table SHA-256 and a combined `content_hash`.
`tests/unit/test_seed.py` checks this, and checks that every trap above is present.
