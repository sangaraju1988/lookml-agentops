from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import EXAMPLE, REPO

from lookml_agentops.config import load_config
from lookml_agentops.inputs.declared import declared_inputs
from lookml_agentops.inputs.model import input_id, kind_of
from lookml_agentops.inputs.owners import OwnerRule, Owners


def test_owner_resolution_most_specific_wins() -> None:
    owners = Owners(
        [
            OwnerRule(match="lookml:*", team="platform"),
            OwnerRule(match="lookml:core_project/**", team="central"),
            OwnerRule(match="lookml:core_project/views/orders.view.lkml", team="revenue-team"),
            OwnerRule(match="runner:*", team="bi"),
        ]
    )
    assert owners.owner("lookml:core_project") == "central"
    assert owners.owner("lookml:core_project", "views/orders.view.lkml") == "revenue-team"
    assert owners.owner("lookml:core_project", "views/customers.view.lkml") == "central"
    assert owners.owner("lookml:other_project") == "platform"
    assert owners.owner("runner:mock") == "bi"
    assert owners.owner("agent:x") == "unassigned"


def test_example_declares_every_input_with_an_owner() -> None:
    cfg = load_config(EXAMPLE)
    inputs = {i.input_id: i for i in declared_inputs(cfg)}
    assert set(inputs) == {
        "lookml:core_project",
        "lookml:finance_project",
        "lookml:logistics_project",
        "lookml:procurement_project",
        "catalog:glossary",
        "suite:finance",
        "suite:logistics",
        "suite:procurement",
        "config:lkagent",
    }
    assert inputs["lookml:core_project"].owner == "central-data-platform"
    assert inputs["lookml:procurement_project"].owner == "procurement-analytics"
    assert inputs["config:lkagent"].owner == "unassigned"
    for i in inputs.values():
        assert re.fullmatch(
            r"([0-9a-f]{40}(\+dirty\.[0-9a-f]{12})?|sha256:[0-9a-f]{16})", i.version
        ), i
    assert kind_of(input_id("agent_spec", "x")) == "agent_spec"
    assert len(Owners.load(EXAMPLE / "owners.yaml").teams()) >= 4


def test_input_version_is_content_hash_outside_git(tmp_path: Path) -> None:
    from lookml_agentops._util.gitinfo import input_version

    (tmp_path / "a.lkml").write_text("view: a {}\n")
    v1 = input_version(tmp_path)
    assert v1.startswith("sha256:") and v1 == input_version(tmp_path)
    (tmp_path / "a.lkml").write_text("view: b {}\n")
    assert input_version(tmp_path) != v1


def test_no_topology_words_in_src() -> None:
    """The code has no built-in hub-and-spoke concept (R1 acceptance)."""
    offenders = []
    for path in sorted((REPO / "src").rglob("*")):
        if path.is_file() and path.suffix in {".py", ".j2", ".md", ".yaml", ".json"}:
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if re.search(
                    r"\b(hub|hubs|spoke|spokes)\b|_hub\b|hub_|_spoke|spoke_", line, re.IGNORECASE
                ):
                    offenders.append(f"{path.relative_to(REPO)}:{n}: {line.strip()}")
    assert offenders == []
