"""Seed deltas: deterministic changes applied on top of the base dataset.

``late-refunds`` appends refunds that arrive a few days before ``as_of`` for orders placed in the
last completed fiscal quarter. Net revenue attributes refunds to the order's period, so the
ground truth of every "net revenue last quarter" question moves even though no LookML, spec or
test changed. That is data drift, not an agent regression.
"""

from __future__ import annotations

import datetime as dt

from lookml_agentops.seed import fiscal
from lookml_agentops.seed.generator import SeedResult, _epoch, _rng

DELTAS = ("late-refunds",)


def apply_delta(res: SeedResult, name: str, seed: int, as_of: dt.date) -> None:
    if name not in DELTAS:
        raise ValueError(f"unknown seed delta {name!r}; choose from {list(DELTAS)}")
    t = res.tables
    start, end, _ = fiscal.last_completed_fiscal_quarter(as_of)
    s_ep, e_ep = _epoch(start), _epoch(end)
    orders = t["orders"]
    test_customers = {
        cid
        for cid, test in zip(
            t["customers"]["customer_id"], t["customers"]["is_test_account"], strict=True
        )
        if test
    }
    refunded = set(t["refunds"]["order_id"])
    cands = [
        (oid, gross - disc)
        for oid, ts, cid, gross, disc, deleted in zip(
            orders["order_id"],
            orders["order_ts_utc"],
            orders["customer_id"],
            orders["gross_amount"],
            orders["discount_amount"],
            orders["is_deleted"],
            strict=True,
        )
        if s_ep <= ts < e_ep and not deleted and cid not in test_customers and oid not in refunded
    ]
    r = _rng(seed, "delta:late-refunds")
    n = max(20, len(cands) // 50)
    pick = sorted(r.choice(len(cands), size=min(n, len(cands)), replace=False).tolist())
    refund_ts = _epoch(as_of) - 3 * 86400
    next_id = max(t["refunds"]["refund_id"], default=0) + 1
    for i, idx in enumerate(pick):
        oid, net = cands[idx]
        t["refunds"]["refund_id"].append(next_id + i)
        t["refunds"]["order_id"].append(oid)
        t["refunds"]["refunded_at_utc"].append(refund_ts + i)
        t["refunds"]["refund_amount"].append(max(1, int(net * 0.4)))
        t["refunds"]["reason"].append("late_adjustment")
