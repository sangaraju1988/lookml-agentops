---
agent: company-baseline
description: Company-wide rules and guardrails that every Harborline data agent inherits.
---

## Rules
- id: revenue-default
  text: '"Revenue" means net revenue unless the user explicitly says gross.'
  locked: true
- id: fiscal-quarter
  text: '"Last quarter" means the last completed fiscal quarter; the fiscal year starts February 1.'
  locked: true
- id: fiscal-year
  text: '"Last year" means the last completed fiscal year (FY2026 = Feb 2025 to Jan 2026).'
  locked: true
- id: last-month
  text: '"Last month" means the last completed calendar month.'
- id: exclude-test-accounts
  text: Never include test accounts or soft-deleted orders, even when asked for all data.
  locked: true
- id: state-the-period
  text: Always state the time period an answer covers.

## Guardrails
- id: no-customer-pii
  text: Never return customer email, phone, date of birth, billing address or contact name.
