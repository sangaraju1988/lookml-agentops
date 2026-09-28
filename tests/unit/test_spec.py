from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import EXAMPLE

from lookml_agentops._util.hashing import canonical_json
from lookml_agentops.spec.errors import SpecError
from lookml_agentops.spec.parse import parse_file, parse_text
from lookml_agentops.spec.render import render_spec
from lookml_agentops.spec.resolve import resolve_spec

SPECS = sorted((EXAMPLE / "agents").rglob("*.agent.md"))


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _strip_lines(ps):  # type: ignore[no-untyped-def]
    return (
        ps.front.model_dump(),
        ps.role[0] if ps.role else None,
        ps.audience[0] if ps.audience else None,
        [(r.rule_id, r.text, r.locked) for r in ps.rules],
        [(v.phrases, v.target) for v in ps.vocabulary],
        [(g.guardrail_id, g.text) for g in ps.guardrails],
        [(q.golden_id, q.questions, q.looker_query, q.explore_url) for q in ps.golden],
    )


@pytest.mark.parametrize("path", SPECS, ids=lambda p: p.name)
def test_example_specs_round_trip(path: Path) -> None:
    parsed = parse_file(path, EXAMPLE)
    again = parse_text(render_spec(parsed), path=path, rel=parsed.rel)
    assert _strip_lines(again) == _strip_lines(parsed)
    # rendering is a fixed point
    assert render_spec(again) == render_spec(parsed)


def test_line_numbers_are_kept() -> None:
    parsed = parse_file(EXAMPLE / "agents/shared/company-baseline.agent.md", EXAMPLE)
    lines = (EXAMPLE / "agents/shared/company-baseline.agent.md").read_text().splitlines()
    for r in parsed.rules:
        assert f"id: {r.rule_id}" in lines[r.line - 1]


def test_extends_chain_and_claims() -> None:
    spec = resolve_spec(EXAMPLE / "agents/finance-analyst.agent.md", EXAMPLE)
    assert [c.agent_id for c in spec.extends_chain] == ["company-baseline"]
    rule = next(r for r in spec.rules if r.rule_id == "revenue-default")
    assert rule.locked and rule.origin_agent == "company-baseline"
    assert rule.source.file == "agents/shared/company-baseline.agent.md"
    assert [(c.kind, c.subject, c.value) for c in rule.claims] == [
        ("vocabulary", "revenue", "net revenue")
    ]
    fq = next(r for r in spec.rules if r.rule_id == "fiscal-quarter")
    assert fq.claims[0].value == "last_completed_fiscal_quarter"
    assert spec.guardrails[0].pii_kinds == ["address", "dob", "email", "name", "phone"]
    assert [f"{e.project}::{e.explore}" for e in spec.explores] == [
        "finance_project::finance_orders",
        "finance_project::finance_invoices",
    ]
    assert resolve_spec(EXAMPLE / "agents/shared/company-baseline.agent.md", EXAMPLE).is_abstract


def test_output_is_deterministic() -> None:
    a = resolve_spec(EXAMPLE / "agents/logistics-ops.agent.md", EXAMPLE).finalize()
    b = resolve_spec(EXAMPLE / "agents/logistics-ops.agent.md", EXAMPLE).finalize()
    assert canonical_json(a.model_dump(mode="json")) == canonical_json(b.model_dump(mode="json"))
    assert a.content_hash == b.content_hash


BASE = """---
agent: base
description: base
---
## Rules
- id: revenue-default
  text: '"Revenue" means net revenue.'
  locked: true
- id: sales
  text: '"Sales" means net revenue.'
"""


def test_locked_rule_cannot_be_contradicted(tmp_path: Path) -> None:
    _write(tmp_path, "base.agent.md", BASE)
    child = _write(
        tmp_path,
        "child.agent.md",
        """---
agent: child
description: c
extends: [base.agent.md]
---
## Rules
- id: my-revenue
  text: '"Revenue" means gross revenue.'
""",
    )
    with pytest.raises(SpecError, match="contradicts locked rule 'revenue-default'") as exc:
        resolve_spec(child, tmp_path)
    assert exc.value.rule_id == "LKS008" and exc.value.line == 7


def test_unlocked_rule_can_be_overridden(tmp_path: Path) -> None:
    _write(tmp_path, "base.agent.md", BASE)
    child = _write(
        tmp_path,
        "child.agent.md",
        """---
agent: child
description: c
extends: [base.agent.md]
---
## Rules
- id: sales-gross
  text: '"Sales" means gross revenue.'
""",
    )
    spec = resolve_spec(child, tmp_path)
    assert next(r for r in spec.rules if r.rule_id == "sales").overridden_by == "sales-gross"
    assert [r.rule_id for r in spec.active_rules()] == ["revenue-default", "sales-gross"]


def test_duplicate_rule_id_and_cycles(tmp_path: Path) -> None:
    _write(tmp_path, "base.agent.md", BASE)
    dup = _write(
        tmp_path,
        "dup.agent.md",
        """---
agent: dup
description: d
extends: [base.agent.md]
---
## Rules
- id: sales
  text: Something else.
""",
    )
    with pytest.raises(SpecError, match="duplicate rule id 'sales'") as exc:
        resolve_spec(dup, tmp_path)
    assert exc.value.rule_id == "LKS007"
    _write(tmp_path, "a.agent.md", "---\nagent: a\ndescription: a\nextends: [b.agent.md]\n---\n")
    _write(tmp_path, "b.agent.md", "---\nagent: b\ndescription: b\nextends: [a.agent.md]\n---\n")
    with pytest.raises(SpecError, match=r"extends cycle: a.agent.md -> b.agent.md -> a.agent.md"):
        resolve_spec(tmp_path / "a.agent.md", tmp_path)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            "---\nagent: x\ndescription: d\n---\n## Tone\nfriendly\n",
            "unknown section '## Tone'. Known sections",
        ),
        ("---\nagent: x\ndescription: d\nmodel: m\n---\n", "invalid frontmatter"),
        ("---\nagent: X Y\ndescription: d\n---\n", "must be lowercase"),
        (
            "---\nagent: x\ndescription: d\nexplores: [finance_orders]\n---\n",
            "<project>::<explore>",
        ),
        ("---\nagent: x\ndescription: d\n---\n## Rules\n- text: no id\n", "rule is missing ['id']"),
        (
            "---\nagent: x\ndescription: d\n---\n## Vocabulary\n- DSO = field\n",
            "vocabulary lines look like",
        ),
        ("agent: x\n", "must start with a '---'"),
        (
            "---\nagent: x\ndescription: d\n---\n## Golden queries\n- id: g\n  questions: [q]\n",
            "exactly one of looker_query or explore_url",
        ),
    ],
)
def test_helpful_parse_errors(text: str, message: str) -> None:
    with pytest.raises(
        SpecError, match=message.replace("[", r"\[").replace("]", r"\]").replace("(", r"\(")
    ):
        parse_text(text, path=Path("x.agent.md"), rel="x.agent.md")
