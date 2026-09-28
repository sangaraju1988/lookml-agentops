from __future__ import annotations

from lookml_agentops.lint.engine import LintResult

ICON = {"error": "🔴", "warning": "🟡", "note": "🔵"}


def _cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def render_markdown(result: LintResult) -> str:
    out = ["## lkagent lint", ""]
    out.append(
        f"**{result.errors} error(s)**, {result.warnings} warning(s), "
        f"{len(result.findings)} finding(s) total."
    )
    if not result.findings:
        return "\n".join([*out, "", "No findings. ✅", ""])
    out += ["", "| | Rule | Location | Message |", "|---|---|---|---|"]
    for f in result.findings:
        loc = f"`{f.loc}`" if f.loc else ""
        out.append(f"| {ICON[f.severity]} | {f.rule_id} | {loc} | {_cell(f.message)} |")
    return "\n".join(out) + "\n"
