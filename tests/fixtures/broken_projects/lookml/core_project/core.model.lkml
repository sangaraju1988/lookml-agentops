connection: "harborline_warehouse"
include: "/views/*.view.lkml"
include: "/explores/*.explore.lkml"
include: "/does_not_exist/*.view.lkml"

access_grant: can_view_pii {
  user_attribute: pii_access
  allowed_values: ["yes"]
}
