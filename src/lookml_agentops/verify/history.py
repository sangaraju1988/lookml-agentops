"""Run history in a local DuckDB file (``.lkagent/history.duckdb``).

Stores run metadata (runner, vendor profile, project revisions, instruction hashes) and per-test
results with answer *structure* only — never row-level data.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, Field

from lookml_agentops.verify.comparator import TestResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id VARCHAR PRIMARY KEY,
  seq INTEGER,
  started_at TIMESTAMP,
  label VARCHAR,
  mode VARCHAR,
  runner VARCHAR,
  runner_version VARCHAR,
  vendor_profile VARCHAR,
  as_of DATE,
  hub VARCHAR,
  revisions JSON,
  instruction_hashes JSON,
  data_hash VARCHAR
);
CREATE TABLE IF NOT EXISTS results (
  run_id VARCHAR,
  test_id VARCHAR,
  spoke VARCHAR,
  kind VARCHAR,
  status VARCHAR,
  mode VARCHAR,
  checks JSON,
  details JSON,
  tags JSON,
  rule_ids JSON,
  test_hash VARCHAR,
  answer JSON
);
"""


class RunInfo(BaseModel):
    run_id: str = ""
    seq: int = 0
    started_at: dt.datetime
    label: str | None = None
    mode: str = "nightly"
    runner: str
    runner_version: str
    vendor_profile: str | None = None
    as_of: dt.date
    hub: str
    revisions: dict[str, dict[str, Any]] = Field(
        default_factory=dict
    )  # project -> {tree_hash, commit_sha}
    instruction_hashes: dict[str, dict[str, Any]] = Field(
        default_factory=dict
    )  # spoke -> {content, layers}
    data_hash: str | None = None


class RunRecord(BaseModel):
    info: RunInfo
    results: list[TestResult]


class History:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.con = duckdb.connect(str(path))
        self.con.execute(SCHEMA)

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> History:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def record(self, info: RunInfo, results: list[TestResult]) -> RunInfo:
        row = self.con.execute("SELECT COALESCE(MAX(seq), 0) FROM runs").fetchone()
        seq = int(row[0]) + 1 if row else 1
        info = info.model_copy(update={"seq": seq, "run_id": f"run-{seq:04d}"})
        self.con.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                info.run_id,
                info.seq,
                info.started_at,
                info.label,
                info.mode,
                info.runner,
                info.runner_version,
                info.vendor_profile,
                info.as_of,
                info.hub,
                json.dumps(info.revisions, sort_keys=True),
                json.dumps(info.instruction_hashes, sort_keys=True),
                info.data_hash,
            ],
        )
        self.con.executemany(
            "INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                [
                    info.run_id,
                    r.test_id,
                    r.spoke,
                    r.kind,
                    r.status,
                    r.mode,
                    json.dumps(r.checks, sort_keys=True),
                    json.dumps(r.details),
                    json.dumps(r.tags),
                    json.dumps(r.rule_ids),
                    r.test_hash,
                    json.dumps(r.answer, sort_keys=True),
                ]
                for r in results
            ],
        )
        return info

    def run_ids(self) -> list[str]:
        return [r[0] for r in self.con.execute("SELECT run_id FROM runs ORDER BY seq").fetchall()]

    def load(self, run_id: str) -> RunRecord:
        cols = [d[0] for d in self.con.execute("SELECT * FROM runs LIMIT 0").description]
        row = self.con.execute("SELECT * FROM runs WHERE run_id = ?", [run_id]).fetchone()
        if row is None:
            raise KeyError(f"unknown run {run_id}")
        d = dict(zip(cols, row, strict=True))
        d["revisions"] = json.loads(d["revisions"])
        d["instruction_hashes"] = json.loads(d["instruction_hashes"])
        info = RunInfo.model_validate(d)
        results = [
            TestResult(
                test_id=r[1],
                spoke=r[2],
                kind=r[3],
                status=r[4],
                mode=r[5],
                checks=json.loads(r[6]),
                details=json.loads(r[7]),
                tags=json.loads(r[8]),
                rule_ids=json.loads(r[9]),
                test_hash=r[10],
                answer=json.loads(r[11]),
            )
            for r in self.con.execute(
                "SELECT * FROM results WHERE run_id = ? ORDER BY spoke, test_id", [run_id]
            ).fetchall()
        ]
        return RunRecord(info=info, results=results)

    def latest(self, n: int = 2) -> list[str]:
        return self.run_ids()[-n:]
