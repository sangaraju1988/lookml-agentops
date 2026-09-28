from __future__ import annotations

from lookml_agentops.lint.engine import LintResult


def render_text(result: LintResult) -> str:
    hints = {r.id: r.fix_hint for r in result.rules}
    lines = []
    for f in result.findings:
        where = str(f.loc) if f.loc else "-"
        lines.append(f"{f.severity.upper():<7} {f.rule_id}  {where}  {f.message}")
        lines.append(f"        fix: {hints.get(f.rule_id, '')}")
    lines.append(
        f"{len(result.findings)} finding(s): {result.errors} error(s), {result.warnings} warning(s)"
    )
    return "\n".join(lines) + "\n"
