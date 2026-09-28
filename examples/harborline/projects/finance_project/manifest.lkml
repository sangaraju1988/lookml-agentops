project_name: "finance_project"

# Demo: core_project is a sibling project on the same instance.
local_dependency: {
  project: "core_project"
}

# When the imported project lives in its own repo, pin it instead, e.g.:
# remote_dependency: core_project {
#   url: "https://git.example.com/harborline/core_project.git"
#   ref: "v1.4.0"
# }
