explore: orders_base {
  extension: required
  view_name: orders
  description: "Orders."
  tags: ["fiscal_reporting"]

  join: customers {
    relationship: many_to_one
    sql_on: ${orders.customer_id} = ${customers.customer_id} ;;
  }
}

explore: invoices {
  description: "Invoices."
}
