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
po AS (
  SELECT p.*, f.fiscal_quarter_label, f.fiscal_year
  FROM raw.purchase_orders p
  JOIN raw.fiscal_calendar f ON f.calendar_date = CAST(p.ordered_at_utc AS DATE)
)
SELECT s.supplier_name, SUM(po.amount)
FROM po JOIN raw.suppliers s ON s.supplier_id = po.supplier_id
WHERE po.status <> 'cancelled' AND po.fiscal_year = (SELECT fy FROM cur) - 1
GROUP BY 1 ORDER BY 1
