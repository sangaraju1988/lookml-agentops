view: suppliers {
  sql_table_name: raw.suppliers ;;
  label: "Suppliers"
  description: "Harborline's suppliers."

  dimension: supplier_id {
    primary_key: yes
    hidden: yes
    type: number
    sql: ${TABLE}.supplier_id ;;
  }

  dimension: supplier_name {
    type: string
    label: "Supplier"
    description: "Supplier company name."
    sql: ${TABLE}.supplier_name ;;
    tags: ["ai_exposed", "glossary:supplier"]
  }

  dimension: is_preferred {
    type: yesno
    label: "Preferred Supplier"
    description: "Supplier is on the preferred-supplier list."
    sql: ${TABLE}.is_preferred ;;
  }
}
