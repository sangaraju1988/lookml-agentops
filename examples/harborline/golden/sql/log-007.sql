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
ships AS (  -- warehouse local time -> UTC; normalized status; test accounts excluded
  SELECT s.shipment_id, s.order_id, s.carrier_id, w.warehouse_name,
         CASE WHEN lower(trim(s.status)) = 'shipped' THEN 'Shipped' ELSE trim(s.status) END AS status_norm,
         timezone('UTC', timezone(w.timezone, s.shipped_at_local)) AS shipped_utc,
         timezone('UTC', timezone(w.timezone, s.delivered_at_local)) AS delivered_utc,
         s.delivered_at_local IS NOT NULL AS is_delivered,
         CAST(s.delivered_at_local AS DATE) <= s.promised_date AS is_on_time,
         o.customer_id
  FROM raw.shipments s
  JOIN raw.warehouses w ON w.warehouse_id = s.warehouse_id
  JOIN raw.orders o ON o.order_id = s.order_id
  JOIN raw.customers c ON c.customer_id = o.customer_id
  WHERE NOT c.is_test_account AND NOT o.is_deleted
),
ships_f AS (
  SELECT sh.*, f.fiscal_quarter_label, f.fiscal_year, CAST(sh.shipped_utc AS DATE) AS shipped_date
  FROM ships sh JOIN raw.fiscal_calendar f ON f.calendar_date = CAST(sh.shipped_utc AS DATE)
)
SELECT warehouse_name, COUNT(DISTINCT shipment_id) FROM ships_f WHERE fiscal_quarter_label = (SELECT label FROM last_q) GROUP BY 1 ORDER BY 1
