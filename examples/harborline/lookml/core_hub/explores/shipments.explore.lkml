explore: shipments_base {
  extension: required
  view_name: shipments
  label: "Shipments"
  description: "Shipments with carrier, warehouse and order context. Excludes test accounts and soft-deleted orders."
  tags: ["fiscal_reporting"]
  sql_always_where: NOT ${customers.is_test_account} AND NOT ${orders.is_deleted} ;;

  join: orders {
    type: left_outer
    relationship: one_to_one
    sql_on: ${shipments.order_id} = ${orders.order_id} ;;
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

  join: carriers {
    type: left_outer
    relationship: many_to_one
    sql_on: ${shipments.carrier_id} = ${carriers.carrier_id} ;;
  }

  join: warehouses {
    type: left_outer
    relationship: many_to_one
    sql_on: ${shipments.warehouse_id} = ${warehouses.warehouse_id} ;;
  }

  join: shipped_fiscal {
    from: fiscal_calendar
    view_label: "Shipped Fiscal Period"
    type: left_outer
    relationship: many_to_one
    sql_on: ${shipments.shipped_date} = ${shipped_fiscal.calendar_date} ;;
  }

  join: created_fiscal {
    from: fiscal_calendar
    view_label: "Order Fiscal Period"
    type: left_outer
    relationship: many_to_one
    sql_on: ${orders.created_date} = ${created_fiscal.calendar_date} ;;
  }
}
