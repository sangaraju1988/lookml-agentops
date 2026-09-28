view: +shipments {
  dimension: transit_days {
    hidden: yes
    type: number
    sql: date_diff('hour', ${shipped_raw}, ${delivered_raw}) / 24.0 ;;
  }

  measure: avg_transit_days {
    type: average
    label: "Average Transit Days"
    description: "Average days between leaving the warehouse and delivery (UTC), for delivered shipments."
    sql: ${transit_days} ;;
    value_format_name: decimal_1
    group_label: "Logistics KPIs"
    tags: ["ai_exposed", "glossary:transit_time"]
  }
}

view: +carriers {
  dimension: carrier_name {
    description: "Carrier legal name. 'Northstar Freight' and 'North Star Freight LLC' are different carriers. Never merge them."
  }
}
