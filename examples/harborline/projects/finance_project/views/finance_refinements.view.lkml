# Finance refinements of core_project views. Importing projects may ADD fields and adjust wording,
# but must not redefine the SQL of certified measures they import (lint rule LKA007).

view: +orders {
  measure: gross_revenue {
    group_label: "Finance KPIs"
    description: "Sum of order line amounts before discounts and refunds. Finance calls this 'bookings'. Never use it when someone asks for 'revenue' without qualification."
  }

  measure: refund_rate {
    type: number
    label: "Refund Rate"
    description: "Refunds attributed to the order period divided by gross revenue."
    sql: 1.0 * ${order_refund_facts.total_refunds} / NULLIF(${gross_revenue}, 0) ;;
    value_format_name: percent_2
    group_label: "Finance KPIs"
    tags: ["ai_exposed", "glossary:refund_rate"]
  }
}

view: +payments {
  measure: days_sales_outstanding {
    type: average
    label: "Days Sales Outstanding"
    description: "Average number of days from invoice date to payment date for paid invoices."
    sql: date_diff('day', ${invoices.invoice_raw}, ${payments.payment_raw}) ;;
    value_format_name: decimal_1
    group_label: "Finance KPIs"
    tags: ["ai_exposed", "glossary:days_sales_outstanding"]
  }
}
