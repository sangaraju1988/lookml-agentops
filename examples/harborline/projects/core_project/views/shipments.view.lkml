view: shipments {
  sql_table_name: raw.shipments ;;
  label: "Shipments"
  description: "One shipment per shipped order. Warehouse timestamps are converted from local time to UTC."

  dimension: shipment_id {
    primary_key: yes
    hidden: yes
    type: number
    sql: ${TABLE}.shipment_id ;;
  }

  dimension: order_id {
    hidden: yes
    type: number
    sql: ${TABLE}.order_id ;;
  }

  dimension: carrier_id {
    hidden: yes
    type: number
    sql: ${TABLE}.carrier_id ;;
  }

  dimension: warehouse_id {
    hidden: yes
    type: number
    sql: ${TABLE}.warehouse_id ;;
  }

  dimension: status {
    type: string
    label: "Shipment Status"
    description: "Normalized carrier status. Raw spellings such as 'shipped' and 'SHIPPED ' are normalized to 'Shipped'. 'Shipped - Partial' is a different status."
    sql: CASE WHEN lower(trim(${TABLE}.status)) = 'shipped' THEN 'Shipped' ELSE trim(${TABLE}.status) END ;;
    tags: ["ai_exposed", "glossary:shipment_status"]
  }

  dimension: status_raw {
    hidden: yes
    type: string
    sql: ${TABLE}.status ;;
  }

  dimension_group: shipped {
    type: time
    timeframes: [raw, time, date, week, month, quarter, year]
    convert_tz: no
    label: "Shipped"
    description: "When the shipment left the warehouse, converted from warehouse local time to UTC."
    sql: timezone('UTC', timezone(${warehouses.timezone}, ${TABLE}.shipped_at_local)) ;;
    tags: ["ai_default_time"]
  }

  # lkagent:disable LKA010 reason="delivery timestamps are operational and never reported by fiscal period"
  dimension_group: delivered {
    type: time
    timeframes: [raw, time, date]
    convert_tz: no
    label: "Delivered"
    description: "When the shipment was delivered, converted from warehouse local time to UTC."
    sql: timezone('UTC', timezone(${warehouses.timezone}, ${TABLE}.delivered_at_local)) ;;
  }

  dimension: promised_date {
    hidden: yes
    type: date
    sql: ${TABLE}.promised_date ;;
  }

  dimension: is_delivered {
    hidden: yes
    type: yesno
    sql: ${TABLE}.delivered_at_local IS NOT NULL ;;
  }

  dimension: is_on_time {
    type: yesno
    label: "Delivered On Time"
    description: "Delivered on or before the promised date (both in warehouse local time)."
    sql: CAST(${TABLE}.delivered_at_local AS DATE) <= ${TABLE}.promised_date ;;
  }

  measure: shipment_count {
    type: count
    label: "Shipment Count"
    description: "Number of shipments."
    value_format_name: decimal_0
    tags: ["certified", "ai_exposed", "glossary:shipment_count"]
  }

  measure: delivered_count {
    type: count
    label: "Delivered Shipments"
    description: "Number of shipments with a delivery confirmation."
    filters: [is_delivered: "yes"]
    value_format_name: decimal_0
  }

  measure: on_time_count {
    hidden: yes
    type: count
    filters: [is_on_time: "yes"]
  }

  measure: on_time_delivery_rate {
    type: number
    label: "On-Time Delivery Rate"
    description: "Share of delivered shipments delivered on or before the promised date."
    sql: 1.0 * ${on_time_count} / NULLIF(${delivered_count}, 0) ;;
    value_format_name: percent_1
    tags: ["certified", "ai_exposed", "glossary:on_time_delivery"]
  }
}
