from __future__ import annotations

import re
from pathlib import Path

from lookml_agentops.config import load_config
from lookml_agentops.diagnose.history import History
from lookml_agentops.diagnose.run import RunOptions, run_diagnose
from lookml_agentops.inputs.declared import load_owners
from lookml_agentops.report.build import render_html, render_markdown
from lookml_agentops.report.data import build_report_data
from tests.conftest import REPO


def test_report_groups_verdicts_by_owner_and_is_offline(harbor: Path) -> None:
    cfg = load_config(harbor)
    run_diagnose(cfg, RunOptions(agents=["logistics-ops"]))
    run_diagnose(cfg, RunOptions(agents=["logistics-ops"], scenario="fuzzy_values"))
    with History(cfg.path(cfg.diagnose.history)) as h:
        data = build_report_data(h, load_owners(cfg))
    assert [g.owner for g in data.owners] == ["bi-platform"]
    assert set(data.owners[0].by_cause) == {"external"}
    md = render_markdown(data)
    assert "#### bi-platform (5)" in md and "all trap:dirty-categorical" in md
    assert len(md) < 65000
    html = render_html(data)
    assert "<svg" in html and "bi-platform" in html
    assert not re.search(r"""(src|href)\s*=\s*["']https?://""", html) and "@import" not in html


def test_docs_use_neutral_topology_words() -> None:
    allowed = {"docs/generalization-plan.md", "CHANGELOG.md"}
    offenders = []
    for path in [REPO / "README.md", *sorted((REPO / "docs").glob("*.md"))]:
        rel = path.relative_to(REPO).as_posix()
        if rel in allowed:
            continue
        for n, line in enumerate(path.read_text().splitlines(), start=1):
            if (
                re.search(r"\b(hub|spoke)s?\b", line, re.I)
                and "Hub-and-spoke is just one example" not in line
            ):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert offenders == []
