# procurement_project is standalone, so it carries its own copy of the fiscal calendar view.
view: po_fiscal_calendar {
  sql_table_name: raw.fiscal_calendar ;;
  label: "Fiscal Calendar"
  description: "Harborline fiscal calendar. The fiscal year starts February 1 and is named by the calendar year in which it ends."
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
  }

  dimension: fiscal_quarter_label {
    type: string
    label: "Fiscal Quarter"
    description: "Fiscal quarter such as FY2026-Q3."
    sql: ${TABLE}.fiscal_quarter_label ;;
  }
}
