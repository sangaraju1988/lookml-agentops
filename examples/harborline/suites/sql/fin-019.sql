-- Ground truth computed directly from raw seed tables (never from LookML).
-- $as_of is replaced with the pinned as_of date from lkagent.yaml.
WITH invoices_clean AS (  -- non-void invoices of non-test, non-deleted orders; recognized on invoice date
  SELECT i.*, f.fiscal_quarter_label, f.fiscal_year
  FROM raw.invoices i
  JOIN raw.orders o ON o.order_id = i.order_id
  JOIN raw.customers c ON c.customer_id = o.customer_id
  JOIN raw.fiscal_calendar f ON f.calendar_date = i.invoice_date
  WHERE NOT c.is_test_account AND NOT o.is_deleted AND i.status <> 'void'
)
SELECT fiscal_quarter_label, SUM(amount) FROM invoices_clean GROUP BY 1 ORDER BY 1
