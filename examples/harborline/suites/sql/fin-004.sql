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
orders_clean AS (  -- exclude test accounts and soft-deleted orders
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
SELECT SUM(gross_amount) FROM order_net WHERE fiscal_quarter_label = (SELECT label FROM last_q)
