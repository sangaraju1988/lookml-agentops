-- Ground truth computed directly from raw seed tables (never from LookML).
-- $as_of is replaced with the pinned as_of date from lkagent.yaml.
WITH orders_clean AS (  -- exclude test accounts and soft-deleted orders
  SELECT o.*, CAST(o.order_ts_utc AS DATE) AS order_date
  FROM raw.orders o
  JOIN raw.customers c ON c.customer_id = o.customer_id
  WHERE NOT c.is_test_account AND NOT o.is_deleted
),
refunds_by_order AS (  -- all refunds, attributed to the ORIGINAL order (late refunds included)
  SELECT order_id, SUM(refund_amount) AS refund_amount FROM raw.refunds GROUP BY order_id
),
order_net AS (
  SELECT o.*, o.gross_amount - o.discount_amount - COALESCE(r.refund_amount, 0) AS net_amount,
         COALESCE(r.refund_amount, 0) AS refund_amount, f.fiscal_quarter_label, f.fiscal_year
  FROM orders_clean o
  LEFT JOIN refunds_by_order r ON r.order_id = o.order_id
  JOIN raw.fiscal_calendar f ON f.calendar_date = o.order_date
)
SELECT fiscal_quarter_label, SUM(net_amount) FROM order_net GROUP BY 1 ORDER BY 1
