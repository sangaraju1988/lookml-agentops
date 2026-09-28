view: order_refund_facts {
  label: "Refunds"
  description: "Refunds rolled up to one row per order, regardless of when the refund arrived."

  derived_table: {
    sql: SELECT order_id, SUM(refund_amount) AS refund_amount, COUNT(*) AS refund_count
         FROM raw.refunds
         GROUP BY order_id ;;
  }

  dimension: order_id {
    primary_key: yes
    hidden: yes
    type: number
    sql: ${TABLE}.order_id ;;
  }

  dimension: refund_amount {
    hidden: yes
    type: number
    sql: ${TABLE}.refund_amount ;;
  }

  measure: total_refunds {
    type: sum
    label: "Total Refunds"
    description: "Sum of refunds, attributed to the original order."
    sql: ${refund_amount} ;;
    value_format_name: usd
  }
}
