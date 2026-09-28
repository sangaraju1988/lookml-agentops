connection: "harborline_warehouse"

# Hub files first, then this spoke's refinements and explores: refinements apply in include order.
include: "//core_hub/views/*.view.lkml"
include: "//core_hub/explores/*.explore.lkml"
include: "/views/*.view.lkml"
include: "/explores/*.explore.lkml"

access_grant: can_view_pii {
  user_attribute: pii_access
  allowed_values: ["yes"]
}
