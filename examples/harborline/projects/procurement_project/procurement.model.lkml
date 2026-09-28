connection: "harborline_warehouse"

include: "/views/*.view.lkml"

explore: purchase_orders {
  label: "Procurement: Purchase Orders"
  description: "Purchase orders placed with suppliers, by order date and fiscal period. Cancelled orders are excluded from spend."
  tags: ["fiscal_reporting"]

  join: suppliers {
    type: left_outer
    relationship: many_to_one
    sql_on: ${purchase_orders.supplier_id} = ${suppliers.supplier_id} ;;
  }

  join: po_fiscal {
    from: po_fiscal_calendar
    view_label: "PO Fiscal Period"
    type: left_outer
    relationship: many_to_one
    sql_on: ${purchase_orders.ordered_date} = ${po_fiscal.calendar_date} ;;
  }
}
