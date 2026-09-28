from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lookml_agentops.cli import app
from lookml_agentops.config import load_config
from lookml_agentops.generate.compile import bind_all
from lookml_agentops.generate.draft import (
    CommandProvider,
    DraftError,
    DraftRequest,
    StubProvider,
    draft_spec,
)


def test_stub_draft_is_grounded_and_lints_clean(example_copy: Path) -> None:
    cfg = load_config(example_copy)
    req = DraftRequest(
        "fpa-helper",
        "Finance agent for FP&A. Revenue means net revenue, exclude test accounts.",
        ["finance_project::finance_orders"],
    )
    path = draft_spec(cfg, req, StubProvider(), example_copy / "agents/fpa-helper.agent.md")
    text = path.read_text()
    assert "'\"Revenue\" means net revenue.'" in text and "exclude-test-accounts" in text
    assert "## Guardrails" in text  # the explore exposes PII, so the draft must guard it
    assert [f for f in bind_all(load_config(example_copy)).findings if "fpa-helper" in f.file] == []
    assert "orders.net_revenue" in req.prompt() and "pii" in req.prompt()


def test_command_provider_and_invalid_drafts(example_copy: Path, tmp_path: Path) -> None:
    cfg = load_config(example_copy)
    reply = tmp_path / "reply.md"
    reply.write_text(
        "Here you go:\n```markdown\n---\nagent: cmd-agent\ndescription: d\n"
        "explores: [procurement_project::purchase_orders]\n---\n## Role\nHelpful.\n```\n"
    )
    cmd = f'{shlex.quote(sys.executable)} -c "import sys; sys.stdin.read(); print(open({str(reply)!r}).read())"'
    path = draft_spec(
        cfg,
        DraftRequest("cmd-agent", "d", ["procurement_project::purchase_orders"]),
        CommandProvider(cmd),
        example_copy / "agents/cmd-agent.agent.md",
    )
    assert path.read_text().startswith("---\nagent: cmd-agent")
    reply.write_text("---\nagent: other\ndescription: d\n---\n")
    with pytest.raises(DraftError, match="expected 'cmd-agent2'"):
        draft_spec(
            cfg,
            DraftRequest("cmd-agent2", "d", ["procurement_project::purchase_orders"]),
            CommandProvider(cmd),
            example_copy / "agents/cmd-agent2.agent.md",
        )
    with pytest.raises(DraftError, match="not found"):
        draft_spec(
            cfg,
            DraftRequest("x", "d", ["nope::nope"]),
            StubProvider(),
            example_copy / "agents/x.agent.md",
        )
    with pytest.raises(DraftError, match="exists"):
        draft_spec(
            cfg,
            DraftRequest("cmd-agent", "d", ["procurement_project::purchase_orders"]),
            StubProvider(),
            path,
        )


def test_cli_generate_new(example_copy: Path) -> None:
    res = CliRunner().invoke(
        app,
        [
            "generate",
            "new",
            "buyer2",
            "-c",
            str(example_copy),
            "-d",
            "Procurement helper. Spend means total spend.",
            "--explore",
            "procurement_project::purchase_orders",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "lint: 0 finding(s)" in res.output
