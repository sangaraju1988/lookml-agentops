view: invoices {
  sql_table_name: raw.invoices ;;

  dimension: invoice_id {
    primary_key: yes
    hidden: yes
    sql: ${TABLE}.invoice_id ;;
  }

  measure: revenue {
    type: sum
    description: "Invoice revenue."
    sql: ${TABLE}.amount ;;
    value_format_name: usd
  }
}
