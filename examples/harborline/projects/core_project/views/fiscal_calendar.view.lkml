view: fiscal_calendar {
  sql_table_name: raw.fiscal_calendar ;;
  label: "Fiscal Calendar"
  description: "Harborline fiscal calendar. The fiscal year starts February 1 and is named by the calendar year in which it ends (FY2026 = 2025-02-01 to 2026-01-31). 'Last quarter' means the last completed fiscal quarter."
  tags: ["fiscal_calendar"]

  dimension: calendar_date {
    primary_key: yes
    hidden: yes
    type: date
    sql: ${TABLE}.calendar_date ;;
  }

  dimension: fiscal_year {
    type: number
    label: "Fiscal Year"
    description: "Fiscal year, named by the calendar year in which it ends."
    sql: ${TABLE}.fiscal_year ;;
    value_format_name: id
    tags: ["ai_exposed", "glossary:fiscal_year"]
  }

  dimension: fiscal_quarter_label {
    type: string
    label: "Fiscal Quarter"
    description: "Fiscal quarter such as FY2026-Q3."
    sql: ${TABLE}.fiscal_quarter_label ;;
    tags: ["ai_exposed", "glossary:fiscal_quarter"]
  }

  dimension: fiscal_quarter {
    hidden: yes
    type: number
    label: "Fiscal Quarter Number"
    sql: ${TABLE}.fiscal_quarter ;;
  }

  dimension: fiscal_month {
    type: number
    label: "Fiscal Month Number"
    description: "Month of the fiscal year, 1 = February."
    sql: ${TABLE}.fiscal_month ;;
  }
}
