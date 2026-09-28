view: invoices {
  sql_table_name: raw.invoices ;;
  label: "Invoices"
  description: "One invoice per shipped order. Revenue is recognized on the invoice date."

  dimension: invoice_id {
    primary_key: yes
    hidden: yes
    type: number
    sql: ${TABLE}.invoice_id ;;
  }

  dimension: order_id {
    hidden: yes
    type: number
    sql: ${TABLE}.order_id ;;
  }

  dimension: status {
    type: string
    label: "Invoice Status"
    description: "open, paid or void."
    sql: ${TABLE}.status ;;
  }

  dimension: is_void {
    type: yesno
    label: "Is Void"
    description: "Voided invoices never count as revenue."
    sql: ${TABLE}.status = 'void' ;;
  }

  dimension_group: invoice {
    type: time
    datatype: date
    timeframes: [raw, date, week, month, quarter, year]
    convert_tz: no
    label: "Invoice"
    description: "Invoice date, which is the revenue recognition date."
    sql: ${TABLE}.invoice_date ;;
    tags: ["ai_default_time"]
  }

  dimension: amount {
    hidden: yes
    type: number
    sql: ${TABLE}.amount ;;
  }

  measure: recognized_revenue {
    type: sum
    label: "Recognized Revenue"
    description: "Invoiced amount (net of discounts) recognized on the invoice date, excluding void invoices."
    sql: ${amount} ;;
    filters: [is_void: "no"]
    value_format_name: usd
    tags: ["certified", "ai_exposed", "glossary:recognized_revenue"]
  }

  measure: invoice_count {
    type: count
    label: "Invoice Count"
    description: "Number of invoices."
    value_format_name: decimal_0
  }
}
