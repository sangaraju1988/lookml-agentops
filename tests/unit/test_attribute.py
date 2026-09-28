from __future__ import annotations

import datetime as dt

from lookml_agentops.attribute.attribute import attribute, headline
from lookml_agentops.verify.comparator import TestResult
from lookml_agentops.verify.history import RunInfo, RunRecord


def _info(
    run_id: str,
    *,
    hub: str = "h1",
    fin: str = "f1",
    runner: str = "mock-1.0/v1",
    hub_layer: str = "L1",
    data: str = "d1",
) -> RunInfo:
    return RunInfo(
        run_id=run_id,
        started_at=dt.datetime(2026, 1, 1),
        runner="mock",
        runner_version=runner,
        as_of=dt.date(2026, 1, 20),
        hub="core_hub",
        revisions={"core_hub": {"tree_hash": hub}, "finance_spoke": {"tree_hash": fin}},
        instruction_hashes={
            "finance_spoke": {"layers": {"hub:core_hub": hub_layer, "spoke:finance_spoke": "S"}}
        },
        data_hash=data,
    )


def _res(
    test_id: str, status: str, tags: list[str] | None = None, test_hash: str = "t"
) -> TestResult:
    return TestResult(
        test_id=test_id,
        spoke="finance_spoke",
        kind="golden",
        status=status,  # type: ignore[arg-type]
        mode="structural",
        tags=tags or [],
        test_hash=test_hash,
    )


def _run(info: RunInfo, *results: TestResult) -> RunRecord:
    return RunRecord(info=info, results=list(results))


def test_vendor_when_nothing_recorded_changed() -> None:
    base = _run(_info("run-0001"), _res("a", "pass", ["trap:dirty-categorical"]), _res("b", "pass"))
    head = _run(
        _info("run-0002", runner="mock-1.0/v2"),
        _res("a", "fail", ["trap:dirty-categorical"]),
        _res("b", "pass"),
    )
    att = attribute(base, head)
    assert [(c.test_id, c.cause, c.direction) for c in att.changes] == [
        ("a", "vendor", "regression")
    ]
    assert att.unchanged == 1
    assert att.vendor_groups[0].tag == "trap:dirty-categorical" and att.vendor_groups[0].all_changes
    assert "all trap:dirty-categorical" in headline(att)[1]


def test_hub_spoke_and_unknown() -> None:
    base = _run(_info("run-0001"), _res("a", "pass"))
    assert (
        attribute(base, _run(_info("run-0002", hub="h2"), _res("a", "fail"))).changes[0].cause
        == "hub"
    )
    assert (
        attribute(base, _run(_info("run-0002", hub_layer="L2"), _res("a", "fail"))).changes[0].cause
        == "hub"
    )
    assert (
        attribute(base, _run(_info("run-0002", fin="f2"), _res("a", "fail"))).changes[0].cause
        == "spoke"
    )
    both = attribute(base, _run(_info("run-0002", hub="h2", fin="f2"), _res("a", "fail"))).changes[
        0
    ]
    assert both.cause == "unknown" and both.changed == ["hub", "spoke"]
    data = attribute(base, _run(_info("run-0002", data="d2"), _res("a", "fail"))).changes[0]
    assert data.cause == "unknown" and data.changed == ["seed data"]
    edited = attribute(base, _run(_info("run-0002"), _res("a", "fail", test_hash="t2"))).changes[0]
    assert edited.cause == "unknown" and edited.changed == ["golden test"]


def test_fixes_are_attributed_too() -> None:
    base = _run(_info("run-0001"), _res("a", "fail"))
    att = attribute(base, _run(_info("run-0002", hub="h2"), _res("a", "pass")))
    assert att.changes[0].direction == "fix" and att.changes[0].cause == "hub"
