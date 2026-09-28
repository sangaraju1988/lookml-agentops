from __future__ import annotations

from pathlib import Path

import pytest

from lookml_agentops.config import LkagentConfig, load_config
from lookml_agentops.verify.history import History
from lookml_agentops.verify.run import VerifyOptions, run_verify
from tests.conftest import AS_OF, TEST_SCALE

V2_FAIL = {"log-002", "log-004", "log-005", "log-009", "log-015"}
V3_FAIL = {
    "fin-002",
    "fin-007",
    "log-011",
    "adh.vocab.net_revenue.1",
    "adh.vocab.net_revenue.2",
}


@pytest.fixture
def cfg(example_copy: Path, small_seed: Path) -> LkagentConfig:
    import shutil

    c = load_config(example_copy)
    dst = c.path(c.seed.duckdb)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(small_seed, dst)
    return c


def _failing(cfg: LkagentConfig, profile: str) -> set[str]:
    rec = run_verify(cfg, VerifyOptions(profile=profile))
    return {r.test_id for r in rec.results if r.status != "pass"}


def test_v1_passes_everything(cfg: LkagentConfig) -> None:
    rec = run_verify(cfg, VerifyOptions(profile="v1"))
    bad = [(r.test_id, r.details) for r in rec.results if r.status != "pass"]
    assert bad == []
    assert len([r for r in rec.results if r.kind == "golden"]) == 37
    assert rec.info.run_id == "run-0001"
    assert cfg.as_of == AS_OF and TEST_SCALE < 1


def test_vendor_profiles_fail_exactly_as_documented(cfg: LkagentConfig) -> None:
    assert _failing(cfg, "v2_fuzzy_values") == V2_FAIL
    assert _failing(cfg, "v3_instruction_override") == V3_FAIL


def test_every_trap_is_covered_by_a_golden_test(cfg: LkagentConfig) -> None:
    from tests.conftest import EXAMPLE

    traps = set()
    for line in (EXAMPLE / "seed/TRAPS.md").read_text().splitlines():
        if line.startswith("| `trap:"):
            traps.add(line.split("`")[1])
    from lookml_agentops.verify.loader import load_golden

    covered = {t for lt in load_golden(cfg) for t in lt.test.tags}
    assert traps <= covered, traps - covered


def test_history_round_trip(cfg: LkagentConfig) -> None:
    rec = run_verify(cfg, VerifyOptions(profile="v1", label="baseline"))
    with History(cfg.path(cfg.verify.history)) as h:
        loaded = h.load(rec.info.run_id)
    assert loaded.info.label == "baseline"
    assert (
        loaded.info.revisions["core_hub"]["tree_hash"]
        == rec.info.revisions["core_hub"]["tree_hash"]
    )
    assert set(loaded.info.instruction_hashes) == {"finance_spoke", "logistics_spoke"}
    assert {r.test_id for r in loaded.results} == {r.test_id for r in rec.results}
    # no row-level data is stored: only counts and a fingerprint
    for r in loaded.results:
        assert "rows" not in r.answer


def test_generated_tests_match_committed_build(cfg: LkagentConfig) -> None:
    import yaml

    from lookml_agentops.compile.build import compile_agents
    from lookml_agentops.verify.models import TestFile
    from tests.conftest import EXAMPLE

    agents = compile_agents(cfg)
    for spoke, a in agents.items():
        committed = TestFile.model_validate(
            yaml.safe_load((EXAMPLE / "build" / spoke / "tests.generated.yaml").read_text())
        )
        assert [t.id for t in committed.tests] == [t.id for t in a.tests]
