"""Harborline fiscal calendar: the fiscal year starts February 1 and is named by the calendar
year in which it ends (FY2026 = 2025-02-01 .. 2026-01-31)."""

from __future__ import annotations

import datetime as dt

FISCAL_START_MONTH = 2


def fiscal_year(d: dt.date) -> int:
    return d.year + 1 if d.month >= FISCAL_START_MONTH else d.year


def fiscal_month(d: dt.date) -> int:
    return (d.month - FISCAL_START_MONTH) % 12 + 1


def fiscal_quarter(d: dt.date) -> int:
    return (fiscal_month(d) - 1) // 3 + 1


def fiscal_year_start(fy: int) -> dt.date:
    return dt.date(fy - 1, FISCAL_START_MONTH, 1)


def _add_months(d: dt.date, months: int) -> dt.date:
    idx = d.year * 12 + (d.month - 1) + months
    return dt.date(idx // 12, idx % 12 + 1, 1)


def fiscal_quarter_start(fy: int, q: int) -> dt.date:
    return _add_months(fiscal_year_start(fy), 3 * (q - 1))


def quarter_label(fy: int, q: int) -> str:
    return f"FY{fy}-Q{q}"


def last_completed_fiscal_quarter(as_of: dt.date) -> tuple[dt.date, dt.date, str]:
    """Return ``[start, end)`` and label of the last fiscal quarter fully before ``as_of``."""
    fy, q = fiscal_year(as_of), fiscal_quarter(as_of)
    cur_start = fiscal_quarter_start(fy, q)
    prev_start = _add_months(cur_start, -3)
    return prev_start, cur_start, quarter_label(fiscal_year(prev_start), fiscal_quarter(prev_start))


def last_completed_fiscal_year(as_of: dt.date) -> tuple[dt.date, dt.date, str]:
    fy = fiscal_year(as_of) - 1
    return fiscal_year_start(fy), fiscal_year_start(fy + 1), f"FY{fy}"


def fiscal_year_range(fy: int) -> tuple[dt.date, dt.date, str]:
    return fiscal_year_start(fy), fiscal_year_start(fy + 1), f"FY{fy}"


def fiscal_quarter_range(fy: int, q: int) -> tuple[dt.date, dt.date, str]:
    start = fiscal_quarter_start(fy, q)
    return start, _add_months(start, 3), quarter_label(fy, q)


def last_completed_month(as_of: dt.date) -> tuple[dt.date, dt.date, str]:
    cur = dt.date(as_of.year, as_of.month, 1)
    prev = _add_months(cur, -1)
    return prev, cur, prev.strftime("%Y-%m")
