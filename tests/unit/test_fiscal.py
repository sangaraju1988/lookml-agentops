import datetime as dt

from lookml_agentops.seed import fiscal


def test_fiscal_year_named_by_end_year() -> None:
    assert fiscal.fiscal_year(dt.date(2025, 2, 1)) == 2026
    assert fiscal.fiscal_year(dt.date(2026, 1, 31)) == 2026
    assert fiscal.fiscal_year(dt.date(2025, 1, 31)) == 2025


def test_quarters() -> None:
    assert fiscal.fiscal_quarter(dt.date(2025, 2, 1)) == 1
    assert fiscal.fiscal_quarter(dt.date(2025, 4, 30)) == 1
    assert fiscal.fiscal_quarter(dt.date(2025, 5, 1)) == 2
    assert fiscal.fiscal_quarter(dt.date(2026, 1, 15)) == 4


def test_last_completed_quarter_and_year() -> None:
    as_of = dt.date(2026, 1, 20)
    assert fiscal.last_completed_fiscal_quarter(as_of) == (
        dt.date(2025, 8, 1),
        dt.date(2025, 11, 1),
        "FY2026-Q3",
    )
    assert fiscal.last_completed_fiscal_year(as_of) == (
        dt.date(2024, 2, 1),
        dt.date(2025, 2, 1),
        "FY2025",
    )
    assert fiscal.last_completed_month(as_of)[2] == "2025-12"
