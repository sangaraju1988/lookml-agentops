# Validation model for core_project itself. Importing projects cannot import model files, so each
# declares its own model (and access grants) and includes these views and explores.
connection: "harborline_warehouse"

include: "/views/*.view.lkml"
include: "/explores/*.explore.lkml"

access_grant: can_view_pii {
  user_attribute: pii_access
  allowed_values: ["yes"]
}
