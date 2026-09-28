view: orders {
  sql_table_name: raw.orders ;;
  label: "Orders"
  description: "One row per customer order. Amounts are USD. Timestamps are UTC."

  dimension: order_id {
    primary_key: yes
    type: number
    label: "Order ID"
    description: "Unique identifier of the order."
    sql: ${TABLE}.order_id ;;
  }

  dimension: customer_id {
    hidden: yes
    type: number
    sql: ${TABLE}.customer_id ;;
  }

  dimension: warehouse_id {
    hidden: yes
    type: number
    sql: ${TABLE}.warehouse_id ;;
  }

  dimension: channel {
    type: string
    label: "Order Channel"
    description: "Channel the order was placed through: edi, phone or web."
    sql: ${TABLE}.channel ;;
  }

  dimension: is_deleted {
    hidden: yes
    type: yesno
    description: "Soft-deleted order. Always excluded from reporting."
    sql: ${TABLE}.is_deleted ;;
  }

  dimension: gross_amount {
    hidden: yes
    type: number
    sql: ${TABLE}.gross_amount ;;
  }

  dimension: discount_amount {
    hidden: yes
    type: number
    sql: ${TABLE}.discount_amount ;;
  }

  dimension_group: created {
    type: time
    timeframes: [raw, time, date, week, month, quarter, year]
    convert_tz: no
    label: "Order Created"
    description: "When the order was placed (UTC). Use the Order Fiscal Period fields for fiscal reporting."
    sql: ${TABLE}.order_ts_utc ;;
    tags: ["ai_default_time"]
  }

  measure: order_count {
    type: count
    label: "Order Count"
    description: "Number of orders (excluding test accounts and soft-deleted orders)."
    value_format_name: decimal_0
    tags: ["certified", "ai_exposed", "glossary:order_count"]
  }

  measure: gross_revenue {
    type: sum
    label: "Gross Revenue"
    description: "Sum of order line amounts before discounts and refunds."
    sql: ${gross_amount} ;;
    value_format_name: usd
    tags: ["certified", "ai_exposed", "glossary:gross_revenue"]
  }

  measure: total_discounts {
    type: sum
    label: "Total Discounts"
    description: "Sum of order-level discounts."
    sql: ${discount_amount} ;;
    value_format_name: usd
  }

  measure: net_revenue {
    type: sum
    label: "Net Revenue"
    description: "Gross revenue minus discounts minus refunds, attributed to the order date (late refunds reduce the period of the original order)."
    sql: ${gross_amount} - ${discount_amount} - COALESCE(${order_refund_facts.refund_amount}, 0) ;;
    value_format_name: usd
    tags: ["certified", "ai_exposed", "glossary:net_revenue"]
  }

  measure: active_customer_count {
    type: count_distinct
    label: "Active Customers"
    description: "Distinct customers with at least one order in the period."
    sql: ${customer_id} ;;
    value_format_name: decimal_0
    tags: ["certified", "ai_exposed", "glossary:active_customer"]
  }

  measure: average_order_value {
    type: number
    label: "Average Order Value"
    description: "Net revenue divided by order count."
    sql: ${net_revenue} / NULLIF(${order_count}, 0) ;;
    value_format_name: usd
  }
}
