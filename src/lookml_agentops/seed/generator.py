"""Deterministic generator for the Harborline Supply Co. dataset.

Design notes:

* Every table draws from its own ``numpy`` generator keyed by ``(seed, table name)`` so adding a
  column to one table never perturbs another.
* Money is generated and stored as integer cents and only formatted at write time, so there is
  no float drift in the CSVs.
* Timestamps are integer epoch seconds (UTC) internally. Warehouse-local timestamps are naive
  wall-clock values in the warehouse's IANA time zone (the time-zone trap).
* All values are synthetic. PII-shaped columns use Faker with a fixed seed and reserved
  ``example.*`` domains / ``555-01xx`` phone numbers.
"""

from __future__ import annotations

import datetime as dt
import zlib
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
from faker import Faker

from lookml_agentops.seed import fiscal

Column = list[Any]
Table = dict[str, Column]

DATA_START = dt.date(2024, 2, 1)  # FY2025 day one

REGIONS = [
    (1, "Northeast"),
    (2, "Southeast"),
    (3, "Midwest"),
    (4, "Southwest"),
    (5, "Pacific Northwest"),
    (6, "Pacific NW"),  # legacy duplicate of region 5 (dirty categorical trap)
    (7, "Mountain"),
]
REGION_WEIGHTS = [0.20, 0.18, 0.18, 0.14, 0.10, 0.06, 0.14]

CATEGORIES = [
    "Adhesives & Sealants",
    "Electrical",
    "Fasteners",
    "Hand Tools",
    "HVAC Parts",
    "Janitorial",
    "Material Handling",
    "Office Supplies",
    "Packaging Supplies",
    "Plumbing",
    "Power Tools",
    "Safety Equipment",
]

WAREHOUSES = [
    # id, name, city, tz, region_id
    (1, "Harborline East DC", "Newark", "America/New_York", 1),
    (2, "Harborline South DC", "Atlanta", "America/New_York", 2),
    (3, "Harborline Central DC", "Columbus", "America/Chicago", 3),
    (4, "Harborline Mountain DC", "Denver", "America/Denver", 7),
    (5, "Harborline West DC", "Tacoma", "America/Los_Angeles", 5),
]
# region -> warehouse that serves it
REGION_WAREHOUSE = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 5, 7: 4}

CARRIERS = [
    (1, "Northstar Freight"),
    (2, "North Star Freight LLC"),  # different carrier, near-identical name (fuzzy trap)
    (3, "Tidewater Parcel Co."),
    (4, "Granite Route Express"),
    (5, "Keelhaul Carriers"),
]
CARRIER_WEIGHTS = [0.26, 0.16, 0.22, 0.20, 0.16]
CARRIER_PROMISE_DAYS = {1: 3, 2: 4, 3: 2, 4: 3, 5: 5}
CARRIER_TRANSIT_MEAN_H = {1: 50.0, 2: 80.0, 3: 40.0, 4: 60.0, 5: 100.0}

# Canonical shipment statuses and their weights. "Shipped" is written dirty (see below).
SHIPMENT_STATUSES = [
    "Delivered",
    "Shipped",
    "Shipped - Partial",
    "In Transit",
    "Delayed",
    "Returned",
]
SHIPMENT_STATUS_WEIGHTS = [0.55, 0.22, 0.08, 0.06, 0.05, 0.04]
SHIPPED_SPELLINGS = ["Shipped", "shipped", "SHIPPED "]
SHIPPED_SPELLING_WEIGHTS = [0.70, 0.15, 0.15]

SEGMENTS = ["Enterprise", "Mid-Market", "SMB"]
SEGMENT_WEIGHTS = [0.15, 0.35, 0.50]
CHANNELS = ["edi", "phone", "web"]
CHANNEL_WEIGHTS = [0.30, 0.20, 0.50]
REFUND_REASONS = ["damaged", "late_delivery", "pricing_adjustment", "wrong_item"]

PRODUCT_ADJ = ["Heavy-Duty", "Standard", "Compact", "Industrial", "Premium", "Economy"]
PRODUCT_NOUN = {
    "Adhesives & Sealants": ["Epoxy", "Sealant Tube", "Thread Locker"],
    "Electrical": ["Cable Tie Pack", "Junction Box", "Wire Spool"],
    "Fasteners": ["Hex Bolt Box", "Anchor Kit", "Wood Screw Box"],
    "Hand Tools": ["Wrench Set", "Pry Bar", "Utility Knife"],
    "HVAC Parts": ["Air Filter", "Blower Belt", "Thermostat"],
    "Janitorial": ["Mop Kit", "Floor Cleaner", "Trash Liner Case"],
    "Material Handling": ["Pallet Jack", "Hand Truck", "Shelf Bin"],
    "Office Supplies": ["Label Roll", "Copy Paper Case", "Marker Pack"],
    "Packaging Supplies": ["Stretch Wrap", "Box Bundle", "Tape Case"],
    "Plumbing": ["Ball Valve", "PEX Coil", "Pipe Fitting Kit"],
    "Power Tools": ["Cordless Drill", "Angle Grinder", "Impact Driver"],
    "Safety Equipment": ["Hard Hat", "Safety Glasses Box", "Hi-Vis Vest Pack"],
}


@dataclass
class SeedResult:
    tables: dict[str, Table] = field(default_factory=dict)


def _rng(seed: int, name: str) -> np.random.Generator:
    return np.random.default_rng([seed, zlib.crc32(name.encode("utf-8"))])


def _epoch(d: dt.date) -> int:
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.UTC).timestamp())


def _utc_to_local(epochs: np.ndarray, tzs: list[str]) -> list[int]:
    """Convert UTC epoch seconds to naive local wall-clock epoch seconds."""
    out: list[int] = []
    cache: dict[str, ZoneInfo] = {}
    for e, tz in zip(epochs.tolist(), tzs, strict=True):
        zi = cache.setdefault(tz, ZoneInfo(tz))
        local = dt.datetime.fromtimestamp(e, tz=dt.UTC).astimezone(zi)
        off = local.utcoffset()
        out.append(e + int(off.total_seconds()) if off is not None else e)
    return out


def generate(seed: int, as_of: dt.date, scale: float = 1.0) -> SeedResult:
    """Generate every table. Pure function of ``(seed, as_of, scale)``."""
    res = SeedResult()
    t = res.tables
    end_epoch = _epoch(as_of)  # nothing happens on or after as_of
    start_epoch = _epoch(DATA_START)

    n_customers = max(300, int(5000 * scale))
    n_products = 200
    n_orders = max(3000, int(60000 * scale))

    # ---- reference tables ------------------------------------------------------------------
    t["regions"] = {"region_id": [r[0] for r in REGIONS], "region_name": [r[1] for r in REGIONS]}
    t["product_categories"] = {
        "category_id": list(range(1, len(CATEGORIES) + 1)),
        "category_name": list(CATEGORIES),
    }
    t["warehouses"] = {
        "warehouse_id": [w[0] for w in WAREHOUSES],
        "warehouse_name": [w[1] for w in WAREHOUSES],
        "city": [w[2] for w in WAREHOUSES],
        "timezone": [w[3] for w in WAREHOUSES],
        "region_id": [w[4] for w in WAREHOUSES],
    }
    t["carriers"] = {
        "carrier_id": [c[0] for c in CARRIERS],
        "carrier_name": [c[1] for c in CARRIERS],
    }

    # ---- products --------------------------------------------------------------------------
    r = _rng(seed, "products")
    cat_ids = r.integers(1, len(CATEGORIES) + 1, size=n_products)
    list_cents = np.round(np.exp(r.normal(3.6, 0.8, size=n_products)) * 100).astype(np.int64) + 199
    cost_cents = (list_cents * r.uniform(0.45, 0.75, size=n_products)).astype(np.int64)
    adj = r.integers(0, len(PRODUCT_ADJ), size=n_products)
    noun = r.integers(0, 3, size=n_products)
    t["products"] = {
        "product_id": list(range(1, n_products + 1)),
        "sku": [f"HSC-{i:05d}" for i in range(1, n_products + 1)],
        "product_name": [
            f"{PRODUCT_ADJ[a]} {PRODUCT_NOUN[CATEGORIES[c - 1]][n]}"
            for a, c, n in zip(adj.tolist(), cat_ids.tolist(), noun.tolist(), strict=True)
        ],
        "category_id": cat_ids.tolist(),
        "unit_cost": cost_cents.tolist(),
        "list_price": list_cents.tolist(),
    }

    # ---- customers (synthetic PII via seeded Faker) ----------------------------------------
    r = _rng(seed, "customers")
    fake = Faker("en_US")
    fake.seed_instance(seed)
    region_ids = r.choice([x[0] for x in REGIONS], size=n_customers, p=REGION_WEIGHTS)
    segments = r.choice(SEGMENTS, size=n_customers, p=SEGMENT_WEIGHTS)
    is_test = r.random(n_customers) < 0.02
    created = start_epoch - r.integers(30, 900, size=n_customers) * 86400
    companies, contacts, emails, phones, dobs, addrs = [], [], [], [], [], []
    for i in range(n_customers):
        first, last = fake.first_name(), fake.last_name()
        company = fake.company()
        if is_test[i]:
            company = f"TEST ACCOUNT {i + 1:05d}"
        companies.append(company)
        contacts.append(f"{first} {last}")
        emails.append(f"{first}.{last}{i + 1}@example.{['com', 'net', 'org'][i % 3]}".lower())
        phones.append(f"({fake.numerify('###')}) 555-01{fake.numerify('##')}")
        dobs.append(fake.date_of_birth(minimum_age=25, maximum_age=70).isoformat())
        addrs.append(
            f"{fake.street_address()}, {fake.city()}, {fake.state_abbr()} {fake.zipcode()}"
        )
    t["customers"] = {
        "customer_id": list(range(1, n_customers + 1)),
        "company_name": companies,
        "contact_name": contacts,
        "email": emails,
        "phone": phones,
        "date_of_birth": dobs,
        "billing_address": addrs,
        "region_id": region_ids.tolist(),
        "segment": segments.tolist(),
        "created_at": created.tolist(),
        "is_test_account": is_test.tolist(),
    }

    # ---- orders + order_items --------------------------------------------------------------
    r = _rng(seed, "orders")
    span = end_epoch - start_epoch
    # mild growth: density rises linearly ~40% across the period
    u = r.random(n_orders)
    growth = 0.4
    frac = (np.sqrt(1 + growth * (2 + growth) * u) - 1) / growth  # inverse CDF of 1+g*x
    order_ts = np.sort(start_epoch + (frac * (span - 7 * 86400)).astype(np.int64))
    cust_w = r.pareto(1.5, size=n_customers) + 1.0
    cust_w = cust_w / cust_w.sum()
    order_cust = r.choice(np.arange(1, n_customers + 1), size=n_orders, p=cust_w)
    order_wh = np.array([REGION_WAREHOUSE[int(region_ids[c - 1])] for c in order_cust.tolist()])
    channels = r.choice(CHANNELS, size=n_orders, p=CHANNEL_WEIGHTS)
    is_deleted = r.random(n_orders) < 0.005

    n_items_per = 1 + r.poisson(1.5, size=n_orders)
    item_order = np.repeat(np.arange(1, n_orders + 1), n_items_per)
    n_items = int(item_order.size)
    item_prod = r.integers(1, n_products + 1, size=n_items)
    qty = r.integers(1, 21, size=n_items)
    # test accounts buy in bulk so excluding them materially changes totals
    test_mult = np.where(is_test[order_cust[item_order - 1] - 1], 5, 1)
    qty = qty * test_mult
    price_noise = r.uniform(0.9, 1.0, size=n_items)
    unit_cents = np.maximum(100, (list_cents[item_prod - 1] * price_noise).astype(np.int64))
    line_cents = unit_cents * qty
    gross_cents = np.bincount(item_order, weights=line_cents, minlength=n_orders + 1)[1:]
    gross_cents = np.round(gross_cents).astype(np.int64)
    has_disc = r.random(n_orders) < 0.30
    disc_pct = r.uniform(0.02, 0.15, size=n_orders)
    disc_cents = np.where(has_disc, (gross_cents * disc_pct).astype(np.int64), 0)
    net_cents = gross_cents - disc_cents

    t["orders"] = {
        "order_id": list(range(1, n_orders + 1)),
        "customer_id": order_cust.tolist(),
        "warehouse_id": order_wh.tolist(),
        "order_ts_utc": order_ts.tolist(),
        "channel": channels.tolist(),
        "gross_amount": gross_cents.tolist(),
        "discount_amount": disc_cents.tolist(),
        "is_deleted": is_deleted.tolist(),
    }
    t["order_items"] = {
        "order_item_id": list(range(1, n_items + 1)),
        "order_id": item_order.tolist(),
        "product_id": item_prod.tolist(),
        "quantity": qty.tolist(),
        "unit_price": unit_cents.tolist(),
        "line_amount": line_cents.tolist(),
    }

    # ---- shipments + events ----------------------------------------------------------------
    r = _rng(seed, "shipments")
    ship_utc = order_ts + r.integers(4 * 3600, 72 * 3600, size=n_orders)
    shipped_mask = (ship_utc < end_epoch) & ~is_deleted
    ship_order_ids = np.arange(1, n_orders + 1)[shipped_mask]
    n_ship = int(ship_order_ids.size)
    s_ship_utc = ship_utc[shipped_mask]
    s_wh = order_wh[shipped_mask]
    s_carrier = r.choice([c[0] for c in CARRIERS], size=n_ship, p=CARRIER_WEIGHTS)
    s_status_idx = r.choice(len(SHIPMENT_STATUSES), size=n_ship, p=SHIPMENT_STATUS_WEIGHTS)
    spelling = r.choice(len(SHIPPED_SPELLINGS), size=n_ship, p=SHIPPED_SPELLING_WEIGHTS)
    statuses = [
        SHIPPED_SPELLINGS[sp] if SHIPMENT_STATUSES[si] == "Shipped" else SHIPMENT_STATUSES[si]
        for si, sp in zip(s_status_idx.tolist(), spelling.tolist(), strict=True)
    ]
    transit_mean = np.array([CARRIER_TRANSIT_MEAN_H[int(c)] for c in s_carrier.tolist()])
    transit_s = (r.gamma(4.0, transit_mean / 4.0) * 3600).astype(np.int64)
    delivered_utc = s_ship_utc + transit_s
    has_delivery = np.isin(s_status_idx, [0, 5]) & (delivered_utc < end_epoch)  # Delivered/Returned
    wh_tz = {w[0]: w[3] for w in WAREHOUSES}
    tzs = [wh_tz[int(w)] for w in s_wh.tolist()]
    ship_local = _utc_to_local(s_ship_utc, tzs)
    deliv_local = _utc_to_local(delivered_utc, tzs)
    promise_days = np.array([CARRIER_PROMISE_DAYS[int(c)] for c in s_carrier.tolist()])
    promised = [
        (dt.datetime.fromtimestamp(sl, tz=dt.UTC).date() + dt.timedelta(days=int(pd))).isoformat()
        for sl, pd in zip(ship_local, promise_days.tolist(), strict=True)
    ]
    t["shipments"] = {
        "shipment_id": list(range(1, n_ship + 1)),
        "order_id": ship_order_ids.tolist(),
        "warehouse_id": s_wh.tolist(),
        "carrier_id": s_carrier.tolist(),
        "status": statuses,
        "shipped_at_local": ship_local,
        "promised_date": promised,
        "delivered_at_local": [
            d if h else None for d, h in zip(deliv_local, has_delivery.tolist(), strict=True)
        ],
    }
    ev_ship: list[int] = []
    ev_type: list[str] = []
    ev_ts: list[int] = []
    for i in range(n_ship):
        sid = i + 1
        ev_ship += [sid, sid]
        ev_type += ["label_created", "picked_up"]
        ev_ts += [ship_local[i] - 2 * 3600, ship_local[i]]
        if has_delivery[i]:
            ev_ship.append(sid)
            ev_type.append("delivered")
            ev_ts.append(deliv_local[i])
    t["shipment_events"] = {
        "event_id": list(range(1, len(ev_ship) + 1)),
        "shipment_id": ev_ship,
        "event_type": ev_type,
        "event_at_local": ev_ts,
    }

    # ---- invoices + payments ---------------------------------------------------------------
    r = _rng(seed, "invoices")
    inv_epoch = s_ship_utc + r.integers(0, 4, size=n_ship) * 86400
    inv_ok = inv_epoch < end_epoch
    inv_orders = ship_order_ids[inv_ok]
    inv_dates = (inv_epoch[inv_ok] // 86400) * 86400
    n_inv = int(inv_orders.size)
    inv_amount = net_cents[inv_orders - 1]
    is_void = r.random(n_inv) < 0.015
    pay_lag = r.integers(5, 61, size=n_inv) * 86400
    pay_epoch = inv_dates + pay_lag
    paid = (~is_void) & (r.random(n_inv) < 0.95) & (pay_epoch < end_epoch)
    inv_status = np.where(is_void, "void", np.where(paid, "paid", "open"))
    t["invoices"] = {
        "invoice_id": list(range(1, n_inv + 1)),
        "order_id": inv_orders.tolist(),
        "invoice_date": inv_dates.tolist(),
        "amount": inv_amount.tolist(),
        "status": inv_status.tolist(),
    }
    pay_inv = np.arange(1, n_inv + 1)[paid]
    t["payments"] = {
        "payment_id": list(range(1, int(pay_inv.size) + 1)),
        "invoice_id": pay_inv.tolist(),
        "payment_date": ((pay_epoch[paid] // 86400) * 86400).tolist(),
        "amount": inv_amount[paid].tolist(),
    }

    # ---- refunds (some arrive late, after the order's fiscal quarter closed) ---------------
    r = _rng(seed, "refunds")
    refund_mask = (r.random(n_orders) < 0.05) & ~is_deleted
    late = r.random(n_orders) < 0.25
    lag_days = np.where(late, r.integers(45, 161, size=n_orders), r.integers(3, 31, size=n_orders))
    refund_ts = order_ts + lag_days * 86400 + r.integers(0, 86400, size=n_orders)
    refund_mask &= refund_ts < end_epoch
    ref_orders = np.arange(1, n_orders + 1)[refund_mask]
    ref_frac = r.uniform(0.1, 1.0, size=n_orders)[refund_mask]
    ref_cents = np.maximum(1, (net_cents[refund_mask] * ref_frac).astype(np.int64))
    reasons = r.choice(REFUND_REASONS, size=n_orders)[refund_mask]
    t["refunds"] = {
        "refund_id": list(range(1, int(ref_orders.size) + 1)),
        "order_id": ref_orders.tolist(),
        "refunded_at_utc": refund_ts[refund_mask].tolist(),
        "refund_amount": ref_cents.tolist(),
        "reason": reasons.tolist(),
    }

    # ---- procurement (standalone project: suppliers + purchase orders) ----------------------
    r = _rng(seed, "procurement")
    n_sup = 40
    sup_adj = ["Atlas", "Beacon", "Cobalt", "Driftwood", "Ember", "Fjord", "Granite", "Harbor"]
    sup_noun = ["Industrial Supply", "Components", "Materials", "Wholesale", "Manufacturing"]
    t["suppliers"] = {
        "supplier_id": list(range(1, n_sup + 1)),
        "supplier_name": [
            f"{sup_adj[i % len(sup_adj)]} {sup_noun[(i // len(sup_adj)) % len(sup_noun)]}"
            for i in range(n_sup)
        ],
        "category_id": r.integers(1, len(CATEGORIES) + 1, size=n_sup).tolist(),
        "is_preferred": (r.random(n_sup) < 0.3).tolist(),
    }
    n_po = max(500, int(3000 * scale))
    po_ts = np.sort(start_epoch + r.integers(0, end_epoch - start_epoch, size=n_po))
    po_amount = (np.exp(r.normal(8.0, 0.9, size=n_po)) * 100).astype(np.int64)
    po_status = r.choice(["received", "open", "cancelled"], size=n_po, p=[0.8, 0.15, 0.05])
    t["purchase_orders"] = {
        "po_id": list(range(1, n_po + 1)),
        "supplier_id": r.integers(1, n_sup + 1, size=n_po).tolist(),
        "warehouse_id": r.integers(1, len(WAREHOUSES) + 1, size=n_po).tolist(),
        "ordered_at_utc": po_ts.tolist(),
        "amount": po_amount.tolist(),
        "status": po_status.tolist(),
    }

    # ---- fiscal calendar -------------------------------------------------------------------
    days: list[dt.date] = []
    d = dt.date(2023, 2, 1)
    while d < dt.date(2027, 2, 1):
        days.append(d)
        d += dt.timedelta(days=1)
    t["fiscal_calendar"] = {
        "calendar_date": [x.isoformat() for x in days],
        "fiscal_year": [fiscal.fiscal_year(x) for x in days],
        "fiscal_quarter": [fiscal.fiscal_quarter(x) for x in days],
        "fiscal_quarter_label": [
            fiscal.quarter_label(fiscal.fiscal_year(x), fiscal.fiscal_quarter(x)) for x in days
        ],
        "fiscal_month": [fiscal.fiscal_month(x) for x in days],
        "fiscal_year_start": [
            fiscal.fiscal_year_start(fiscal.fiscal_year(x)).isoformat() for x in days
        ],
        "fiscal_quarter_start": [
            fiscal.fiscal_quarter_start(fiscal.fiscal_year(x), fiscal.fiscal_quarter(x)).isoformat()
            for x in days
        ],
    }
    return res
