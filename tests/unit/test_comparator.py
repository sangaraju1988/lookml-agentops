from __future__ import annotations

import datetime as dt

from lookml_agentops.diagnose.comparator import compare, compare_rows
from lookml_agentops.diagnose.loader import LoadedTest
from lookml_agentops.diagnose.models import (
    AgentAnswer,
    Expectation,
    FilterExpect,
    ResolvedFilter,
    RunnerMeta,
    TestCase,
    Tolerance,
)

AS_OF = dt.date(2026, 1, 20)
META = RunnerMeta(runner="mock", runner_version="t")


def _lt(**expect: object) -> LoadedTest:
    return LoadedTest(
        TestCase(id="t", question="q", agent="s", expect=Expectation(**expect)),
        None,
        "h",
        "suite:t",
    )  # type: ignore[arg-type]


def _no_tags(explore: str | None, field: str) -> list[str]:
    return ["pii"] if field == "customers.email" else []


def _ans(**kw: object) -> AgentAnswer:
    return AgentAnswer(meta=kw.pop("meta", META), **kw)  # type: ignore[arg-type]


def test_rows_compare_with_tolerance_and_order() -> None:
    ok, _ = compare_rows(
        [["b", 2.0], ["a", 1.0000001]], [["a", 1.0], ["b", 2.0]], Tolerance(relative=1e-6)
    )
    assert ok
    ok, msg = compare_rows([["a", 1.1]], [["a", 1.0]], Tolerance(relative=1e-6))
    assert not ok and msg == "value 1.1 != expected 1.0 for a"
    assert not compare_rows([["a", 1]], [], Tolerance())[0]


def test_pass_fail_and_degraded() -> None:
    lt = _lt(
        explore="e", fields_any_of=[["v.m"]], filters=[FilterExpect(field="v.d", values=["x"])]
    )
    good = _ans(
        explore="e",
        fields=["v.m"],
        filters=[ResolvedFilter(field="v.d", raw="x", values=["x"])],
        rows=[[1.0]],
    )
    assert compare(lt, good, as_of=AS_OF, truth=[[1.0]], field_tags=_no_tags).status == "pass"
    wrong_explore = good.model_copy(update={"explore": "other"})
    r = compare(lt, wrong_explore, as_of=AS_OF, truth=[[1.0]], field_tags=_no_tags)
    assert r.status == "degraded" and r.checks["explore"] is False
    wrong_value = good.model_copy(update={"rows": [[2.0]]})
    assert (
        compare(lt, wrong_value, as_of=AS_OF, truth=[[1.0]], field_tags=_no_tags).status == "fail"
    )


def test_time_window_resolution() -> None:
    lt = _lt(filters=[FilterExpect(field="o.created_date", period="last_completed_fiscal_quarter")])
    ans = _ans(
        filters=[
            ResolvedFilter(
                field="o.created_date",
                raw="lq",
                start=dt.date(2025, 8, 1),
                end=dt.date(2025, 11, 1),
            )
        ]
    )
    assert compare(lt, ans, as_of=AS_OF, truth=None, field_tags=_no_tags).status == "pass"
    calendar = ans.model_copy(
        update={
            "filters": [
                ResolvedFilter(
                    field="o.created_date",
                    raw="lq",
                    start=dt.date(2025, 10, 1),
                    end=dt.date(2026, 1, 1),
                )
            ]
        }
    )
    assert compare(lt, calendar, as_of=AS_OF, truth=None, field_tags=_no_tags).status == "fail"


def test_pii_refusal_or_exclusion() -> None:
    lt = _lt(refuse_or_exclude_tags=["pii"])
    assert (
        compare(lt, _ans(refused=True), as_of=AS_OF, truth=None, field_tags=_no_tags).status
        == "pass"
    )
    leak = _ans(fields=["customers.email"], rows=[["x"]])
    assert compare(lt, leak, as_of=AS_OF, truth=None, field_tags=_no_tags).status == "fail"


def test_result_only_runner_and_errors() -> None:
    meta = RunnerMeta(runner="ca", runner_version="x", structured=False)
    lt = _lt(explore="e", fields_any_of=[["v.m"]])
    r = compare(lt, _ans(meta=meta, rows=[[1.0]]), as_of=AS_OF, truth=[[1.0]], field_tags=_no_tags)
    assert r.status == "pass" and r.mode == "result_only"
    assert (
        compare(lt, _ans(meta=meta), as_of=AS_OF, truth=None, field_tags=_no_tags).status
        == "skipped"
    )
    assert (
        compare(lt, _ans(error="boom"), as_of=AS_OF, truth=None, field_tags=_no_tags).status
        == "error"
    )


def test_drift_when_ground_truth_moved_from_baseline() -> None:
    lt = _lt(fields_any_of=[["v.m"]])
    ans = _ans(fields=["v.m"], rows=[[2.0]])
    same = compare(lt, ans, as_of=AS_OF, truth=[[2.0]], field_tags=_no_tags)
    moved = compare(
        lt, ans, as_of=AS_OF, truth=[[2.0]], field_tags=_no_tags, baseline_gt_hash="old"
    )
    assert same.status == "pass" and moved.status == "drift"
    assert moved.gt_hash == same.gt_hash and moved.baseline_gt_hash == "old"


def test_context_presence_check() -> None:
    lt = _lt(context_rules=["r1"])
    assert (
        compare(
            lt, _ans(context_rules=["r1", "r2"]), as_of=AS_OF, truth=None, field_tags=_no_tags
        ).status
        == "pass"
    )
    assert (
        compare(lt, _ans(context_rules=["r2"]), as_of=AS_OF, truth=None, field_tags=_no_tags).status
        == "fail"
    )
    assert compare(lt, _ans(), as_of=AS_OF, truth=None, field_tags=_no_tags).status == "skipped"
