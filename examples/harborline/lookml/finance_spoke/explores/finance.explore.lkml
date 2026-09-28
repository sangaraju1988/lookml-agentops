explore: finance_orders {
  extends: [orders_base]
  view_name: orders
  label: "Finance: Orders & Revenue"
  description: "Finance view of orders: net, gross revenue, refunds and customers by fiscal period."
}

explore: finance_invoices {
  extends: [invoices_base]
  view_name: invoices
  label: "Finance: Invoices & Collections"
  description: "Recognized revenue by invoice date, and collections (DSO)."

  join: payments {
    type: left_outer
    relationship: one_to_one
    sql_on: ${invoices.invoice_id} = ${payments.invoice_id} ;;
  }
}
