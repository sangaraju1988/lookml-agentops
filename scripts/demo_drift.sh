#!/usr/bin/env bash
# Offline drift demo: vendor, hub and spoke changes, attributed and reported.
# Runs on a scratch copy of examples/harborline; the checked-in example is never modified.
set -euo pipefail
cd "$(dirname "$0")/.."
WORKDIR="${1:-lkagent-demo}"
uv run lkagent demo --workdir "$WORKDIR"
echo
echo "Open the reports:"
for r in vendor hub spoke; do echo "  $WORKDIR/reports/$r/report.html"; done
