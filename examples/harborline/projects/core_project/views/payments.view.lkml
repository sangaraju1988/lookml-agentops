view: payments {
  sql_table_name: raw.payments ;;
  label: "Payments"
  description: "Customer payments against invoices."

  dimension: payment_id {
    primary_key: yes
    hidden: yes
    type: number
    sql: ${TABLE}.payment_id ;;
  }

  dimension: invoice_id {
    hidden: yes
    type: number
    sql: ${TABLE}.invoice_id ;;
  }

  dimension_group: payment {
    hidden: yes
    type: time
    datatype: date
    timeframes: [raw, date, month]
    convert_tz: no
    sql: ${TABLE}.payment_date ;;
  }

  dimension: amount {
    hidden: yes
    type: number
    sql: ${TABLE}.amount ;;
  }

  measure: total_payments {
    type: sum
    label: "Total Payments"
    description: "Sum of payments received."
    sql: ${amount} ;;
    value_format_name: usd
  }
}
