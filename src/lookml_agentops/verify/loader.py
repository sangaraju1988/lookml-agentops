"""Load golden test files and resolve expected time windows against the pinned as_of."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from lookml_agentops._util.hashing import sha256_obj
from lookml_agentops._util.io import load_yaml
from lookml_agentops.config import LkagentConfig
from lookml_agentops.seed import fiscal
from lookml_agentops.verify.models import FilterExpect, TestCase, TestFile


@dataclass
class LoadedTest:
    test: TestCase
    base_dir: Path | None  # directory ground_truth_sql paths are relative to
    test_hash: str

    @property
    def sql_path(self) -> Path | None:
        gt = self.test.expect.ground_truth_sql
        if gt is None or self.base_dir is None:
            return None
        return self.base_dir / gt


def _hash(test: TestCase, base_dir: Path | None) -> str:
    payload: dict[str, object] = {"test": test.model_dump(mode="json")}
    gt = test.expect.ground_truth_sql
    if gt and base_dir is not None and (base_dir / gt).exists():
        payload["sql"] = (base_dir / gt).read_text(encoding="utf-8")
    return sha256_obj(payload)[:16]


def load_golden(cfg: LkagentConfig) -> list[LoadedTest]:
    out: list[LoadedTest] = []
    seen: set[str] = set()
    for rel in cfg.golden:
        path = cfg.path(rel)
        tf = TestFile.model_validate(load_yaml(path))
        for t in tf.tests:
            if t.id in seen:
                raise ValueError(f"duplicate golden test id {t.id} ({path})")
            seen.add(t.id)
            if t.spoke not in cfg.projects:
                raise ValueError(f"{t.id}: unknown spoke {t.spoke!r}")
            out.append(LoadedTest(t, path.parent, _hash(t, path.parent)))
    return out


def wrap_generated(tests: list[TestCase]) -> list[LoadedTest]:
    return [LoadedTest(t, None, _hash(t, None)) for t in tests]


def expected_window(fe: FilterExpect, as_of: dt.date) -> tuple[dt.date, dt.date] | None:
    if fe.period == "last_completed_fiscal_quarter":
        s, e, _ = fiscal.last_completed_fiscal_quarter(as_of)
    elif fe.period == "last_completed_fiscal_year":
        s, e, _ = fiscal.last_completed_fiscal_year(as_of)
    elif fe.period == "last_completed_month":
        s, e, _ = fiscal.last_completed_month(as_of)
    elif fe.fiscal_year is not None and fe.fiscal_quarter is not None:
        s, e, _ = fiscal.fiscal_quarter_range(fe.fiscal_year, fe.fiscal_quarter)
    elif fe.fiscal_year is not None:
        s, e, _ = fiscal.fiscal_year_range(fe.fiscal_year)
    elif fe.start is not None and fe.end is not None:
        s, e = fe.start, fe.end
    else:
        return None
    return s, e
