explore: orders_base {
  extension: required
  view_name: orders
  label: "Orders"
  description: "Customer orders with gross and net revenue. Excludes test accounts and soft-deleted orders."
  tags: ["fiscal_reporting"]
  sql_always_where: NOT ${customers.is_test_account} AND NOT ${orders.is_deleted} ;;

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

  join: created_fiscal {
    from: fiscal_calendar
    view_label: "Order Fiscal Period"
    type: left_outer
    relationship: many_to_one
    sql_on: ${orders.created_date} = ${created_fiscal.calendar_date} ;;
  }
}
