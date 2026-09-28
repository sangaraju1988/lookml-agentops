connection: "harborline_warehouse"

include: "//core_project/views/*.view.lkml"
include: "//core_project/explores/*.explore.lkml"
include: "/views/*.view.lkml"
include: "/explores/*.explore.lkml"

access_grant: can_view_pii {
  user_attribute: pii_access
  allowed_values: ["yes"]
}
