project_name: "finance_spoke"

# Demo: the hub is a sibling project on the same instance.
local_dependency: {
  project: "core_hub"
}

# Real deployments usually pin the hub from its own repo instead, e.g.:
# remote_dependency: core_hub {
#   url: "https://git.example.com/harborline/core_hub.git"
#   ref: "v1.4.0"
# }
