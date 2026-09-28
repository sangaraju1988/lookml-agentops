view: orders {
  sql_table_name: raw.orders ;;

  dimension: order_id {
    primary_key: yes
    hidden: yes
    sql: ${TABLE}.order_id ;;
  }

  dimension: status {
    sql: ${TABLE}.status ;;
  }

  dimension: customer_id {
    hidden: yes
    sql: ${TABLE}.customer_id ;;
  }

  dimension_group: created {
    type: time
    timeframes: [date, month]
    description: "Order time."
    sql: ${TABLE}.order_ts_utc ;;
  }

  measure: order_count {
    type: count
    label: "Order Count"
    description: "Orders."
    tags: ["certified", "ai_exposed", "glossary:order_count"]
  }

  measure: net_revenue {
    type: sum
    label: "Net Revenue"
    description: "Net revenue."
    sql: ${TABLE}.gross_amount - ${TABLE}.discount_amount ;;
    value_format_name: usd
    tags: ["certified", "ai_exposed", "glossary:net_revenue"]
  }

  measure: gross_revenue {
    type: sum
    label: "Gross Revenue"
    description: "Gross revenue."
    sql: ${TABLE}.gross_amount ;;
    value_format_name: usd
    tags: ["certified", "ai_exposed"]
  }

  measure: revenue {
    type: sum
    description: "Ambiguous revenue."
    sql: ${TABLE}.gross_amount ;;
    value_format_name: usd
  }

  measure: discount_total {
    type: sum
    description: "Discounts."
    sql: ${TABLE}.discount_amount ;;
    value_format_name: usd
    tags: ["ai_exposed", "glossary:does_not_exist"]
  }

  # lkagent:disable LKA001
  measure: hidden_but_exposed {
    hidden: yes
    type: count
    label: "Hidden"
    tags: ["ai_exposed", "glossary:order_count"]
  }
}
