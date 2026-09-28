explore: invoices_base {
  extension: required
  view_name: invoices
  label: "Invoices"
  description: "Invoices and recognized revenue by invoice date. Excludes test accounts, soft-deleted orders and, by default, void invoices."
  tags: ["fiscal_reporting"]
  sql_always_where: NOT ${customers.is_test_account} AND NOT ${orders.is_deleted} ;;

  always_filter: {
    filters: [invoices.is_void: "no"]
  }

  join: orders {
    type: left_outer
    relationship: one_to_one
    sql_on: ${invoices.order_id} = ${orders.order_id} ;;
  }

  join: customers {
    type: left_outer
    relationship: many_to_one
    sql_on: ${orders.customer_id} = ${customers.customer_id} ;;
  }

  join: regions {
    type: left_outer
    relationship: many_to_one
    sql_on: ${customers.region_id} = ${regions.region_id} ;;
  }

  join: order_refund_facts {
    type: left_outer
    relationship: one_to_one
    sql_on: ${orders.order_id} = ${order_refund_facts.order_id} ;;
  }

  join: invoice_fiscal {
    from: fiscal_calendar
    view_label: "Invoice Fiscal Period"
    type: left_outer
    relationship: many_to_one
    sql_on: ${invoices.invoice_date} = ${invoice_fiscal.calendar_date} ;;
  }
}
