view: warehouses {
  sql_table_name: raw.warehouses ;;
  label: "Warehouses"
  description: "Harborline distribution centers. Warehouse systems record timestamps in local time."

  dimension: warehouse_id {
    primary_key: yes
    hidden: yes
    type: number
    sql: ${TABLE}.warehouse_id ;;
  }

  dimension: warehouse_name {
    type: string
    label: "Warehouse"
    description: "Distribution center name."
    sql: ${TABLE}.warehouse_name ;;
    tags: ["ai_exposed", "glossary:warehouse"]
  }

  dimension: timezone {
    hidden: yes
    type: string
    description: "IANA time zone in which the warehouse records timestamps."
    sql: ${TABLE}.timezone ;;
  }
}
