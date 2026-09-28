view: purchase_orders {
  sql_table_name: raw.purchase_orders ;;
  label: "Purchase Orders"
  description: "One row per purchase order placed with a supplier. Amounts are USD; timestamps are UTC."

  dimension: po_id {
    primary_key: yes
    hidden: yes
    type: number
    sql: ${TABLE}.po_id ;;
  }

  dimension: supplier_id {
    hidden: yes
    type: number
    sql: ${TABLE}.supplier_id ;;
  }

  dimension: status {
    type: string
    label: "PO Status"
    description: "received, open or cancelled."
    sql: ${TABLE}.status ;;
    tags: ["ai_exposed", "glossary:po_status"]
  }

  dimension: is_cancelled {
    hidden: yes
    type: yesno
    sql: ${TABLE}.status = 'cancelled' ;;
  }

  dimension_group: ordered {
    type: time
    timeframes: [raw, time, date, week, month, quarter, year]
    convert_tz: no
    label: "Ordered"
    description: "When the purchase order was placed (UTC)."
    sql: ${TABLE}.ordered_at_utc ;;
    tags: ["ai_default_time"]
  }

  dimension: amount {
    hidden: yes
    type: number
    sql: ${TABLE}.amount ;;
  }

  measure: po_count {
    type: count
    label: "PO Count"
    description: "Number of purchase orders."
    value_format_name: decimal_0
    tags: ["certified", "ai_exposed", "glossary:po_count"]
  }

  measure: total_spend {
    type: sum
    label: "Total Spend"
    description: "Sum of purchase order amounts, excluding cancelled orders."
    sql: ${amount} ;;
    filters: [is_cancelled: "no"]
    value_format_name: usd
    tags: ["certified", "ai_exposed", "glossary:spend"]
  }
}
