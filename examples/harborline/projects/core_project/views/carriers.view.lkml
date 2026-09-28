view: carriers {
  sql_table_name: raw.carriers ;;
  label: "Carriers"
  description: "Freight carriers."

  dimension: carrier_id {
    primary_key: yes
    hidden: yes
    type: number
    sql: ${TABLE}.carrier_id ;;
  }

  dimension: carrier_name {
    type: string
    label: "Carrier"
    description: "Carrier legal name."
    sql: ${TABLE}.carrier_name ;;
    tags: ["ai_exposed", "glossary:carrier"]
  }
}
