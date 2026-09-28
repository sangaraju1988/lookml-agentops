# Validation model for the hub itself. Spokes cannot import model files, so each spoke declares
# its own model (and its own access grants) and includes the hub's views and explores.
connection: "harborline_warehouse"

include: "/views/*.view.lkml"
include: "/explores/*.explore.lkml"

access_grant: can_view_pii {
  user_attribute: pii_access
  allowed_values: ["yes"]
}
