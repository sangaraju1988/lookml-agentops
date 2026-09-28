-- Ground truth computed directly from raw seed tables (never from LookML).
-- $as_of is replaced with the pinned as_of date from lkagent.yaml.
WITH ships AS (  -- warehouse local time -> UTC; normalized status; test accounts excluded
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
SELECT fiscal_quarter_label, COUNT(DISTINCT shipment_id) FROM ships_f GROUP BY 1 ORDER BY 1
