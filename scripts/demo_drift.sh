#!/usr/bin/env bash
# Offline demo: external, LookML, spec and data changes, each attributed to an input and owner.
# Runs on a scratch copy of examples/harborline; the checked-in example is never modified.
set -euo pipefail
cd "$(dirname "$0")/.."
WORKDIR="${1:-lkagent-demo}"
uv run lkagent demo --workdir "$WORKDIR"
echo
echo "Reports:"
for r in external lookml spec data; do echo "  $WORKDIR/reports/$r/report.html"; done
