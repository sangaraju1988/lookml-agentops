-- Ground truth computed directly from raw seed tables (never from LookML).
-- $as_of is replaced with the pinned as_of date from lkagent.yaml.
WITH cur AS (
  SELECT fiscal_quarter_start AS q_start, fiscal_year AS fy
  FROM raw.fiscal_calendar WHERE calendar_date = $as_of
),
last_q AS (  -- last COMPLETED fiscal quarter
  SELECT f.fiscal_quarter_label AS label
  FROM raw.fiscal_calendar f, cur
  WHERE f.calendar_date = CAST(cur.q_start - INTERVAL 1 DAY AS DATE)
),
invoices_clean AS (  -- non-void invoices of non-test, non-deleted orders; recognized on invoice date
  SELECT i.*, f.fiscal_quarter_label, f.fiscal_year
  FROM raw.invoices i
  JOIN raw.orders o ON o.order_id = i.order_id
  JOIN raw.customers c ON c.customer_id = o.customer_id
  JOIN raw.fiscal_calendar f ON f.calendar_date = i.invoice_date
  WHERE NOT c.is_test_account AND NOT o.is_deleted AND i.status <> 'void'
)
SELECT SUM(amount) FROM invoices_clean WHERE fiscal_quarter_label = (SELECT label FROM last_q)
