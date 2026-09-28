view: regions {
  sql_table_name: raw.regions ;;
  label: "Regions"
  description: "Sales regions."

  dimension: region_id {
    primary_key: yes
    hidden: yes
    type: number
    sql: ${TABLE}.region_id ;;
  }

  dimension: region_name {
    type: string
    label: "Region"
    description: "Sales region of the customer. The legacy value 'Pacific NW' is normalized to 'Pacific Northwest'."
    sql: CASE WHEN ${TABLE}.region_name = 'Pacific NW' THEN 'Pacific Northwest' ELSE ${TABLE}.region_name END ;;
    tags: ["ai_exposed", "glossary:region"]
  }

  dimension: region_name_raw {
    hidden: yes
    type: string
    description: "Unnormalized region name as stored in the source system."
    sql: ${TABLE}.region_name ;;
  }
}
