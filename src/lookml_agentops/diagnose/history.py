"""Run history (``.lkagent/history.duckdb``), schema version 2.

Tables:

* ``meta``      — ``schema_version``;
* ``runs``      — run id, time, runner + scenario, as_of, agents;
* ``inputs``    — every tracked input of a run (id, kind, version, owner, path, meta);
* ``elements``  — element snapshots (facet versions + per-parameter values/locations);
* ``results``   — per-test outcome, answer *structure* (never rows), ground-truth fingerprint and
  the test's dependency records;
* ``baselines`` — accepted ground-truth fingerprint per test (changes only with ``--rebaseline``).

Migration: a history file without schema version 2 (the v1 layout) is moved aside to
``history.v1.bak.duckdb`` and a fresh v2 store is created. v1 runs lack dependency data, so they
cannot be diagnosed with the v2 attribution; rerun ``lkagent diagnose run`` to rebuild history.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, Field

from lookml_agentops.diagnose.comparator import TestResult
from lookml_agentops.diagnose.elements import Element
from lookml_agentops.inputs.model import TrackedInput

SCHEMA_VERSION = "2"
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key VARCHAR PRIMARY KEY, value VARCHAR);
CREATE TABLE IF NOT EXISTS runs (
  run_id VARCHAR PRIMARY KEY, seq INTEGER, started_at TIMESTAMP, label VARCHAR, runner VARCHAR,
  runner_version VARCHAR, scenario VARCHAR, as_of DATE, agents JSON
);
CREATE TABLE IF NOT EXISTS inputs (
  run_id VARCHAR, input_id VARCHAR, kind VARCHAR, version VARCHAR, owner VARCHAR, path VARCHAR, meta JSON
);
CREATE TABLE IF NOT EXISTS elements (run_id VARCHAR, element_id VARCHAR, kind VARCHAR, facets JSON);
CREATE TABLE IF NOT EXISTS results (
  run_id VARCHAR, test_id VARCHAR, agent VARCHAR, kind VARCHAR, status VARCHAR, mode VARCHAR,
  checks JSON, details JSON, tags JSON, rule_ids JSON, test_hash VARCHAR, answer JSON,
  gt_hash VARCHAR, baseline_gt_hash VARCHAR, deps JSON, question VARCHAR
);
CREATE TABLE IF NOT EXISTS baselines (test_id VARCHAR PRIMARY KEY, gt_hash VARCHAR, run_id VARCHAR);
"""


class RunInfo(BaseModel):
    run_id: str = ""
    seq: int = 0
    started_at: dt.datetime
    label: str | None = None
    runner: str
    runner_version: str
    scenario: str | None = None
    as_of: dt.date
    agents: list[str] = Field(default_factory=list)


class RunRecord(BaseModel):
    info: RunInfo
    inputs: dict[str, TrackedInput] = Field(default_factory=dict)
    elements: dict[str, Element] = Field(default_factory=dict)
    results: list[TestResult] = Field(default_factory=list)

    def result(self, test_id: str) -> TestResult | None:
        return next((r for r in self.results if r.test_id == test_id), None)


def _migrate(path: Path) -> str | None:
    if not path.exists():
        return None
    con = duckdb.connect(str(path))
    try:
        tables = {
            r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
        }
        version = None
        if "meta" in tables:
            row = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            version = row[0] if row else None
    finally:
        con.close()
    if version == SCHEMA_VERSION:
        return None
    backup = path.with_name(path.stem + ".v1.bak.duckdb")
    path.replace(backup)
    return f"history schema v1 detected; moved to {backup.name} and started a fresh v2 history"


class History:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.migration_note = _migrate(path)
        self.con = duckdb.connect(str(path))
        self.con.execute(SCHEMA)
        self.con.execute(
            "INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", [SCHEMA_VERSION]
        )

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> History:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- baselines -------------------------------------------------------------------------
    def baselines(self) -> dict[str, str]:
        return {
            r[0]: r[1]
            for r in self.con.execute("SELECT test_id, gt_hash FROM baselines").fetchall()
        }

    def set_baselines(self, values: dict[str, str], run_id: str) -> None:
        for tid, h in sorted(values.items()):
            self.con.execute("INSERT OR REPLACE INTO baselines VALUES (?, ?, ?)", [tid, h, run_id])

    # ---- runs ------------------------------------------------------------------------------
    def next_run_id(self) -> tuple[str, int]:
        row = self.con.execute("SELECT COALESCE(MAX(seq), 0) FROM runs").fetchone()
        seq = int(row[0]) + 1 if row else 1
        return f"run-{seq:04d}", seq

    def record(self, rec: RunRecord) -> RunInfo:
        run_id, seq = self.next_run_id()
        info = rec.info.model_copy(update={"seq": seq, "run_id": run_id})
        i = info
        self.con.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                seq,
                i.started_at,
                i.label,
                i.runner,
                i.runner_version,
                i.scenario,
                i.as_of,
                json.dumps(i.agents),
            ],
        )
        self.con.executemany(
            "INSERT INTO inputs VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                [
                    run_id,
                    t.input_id,
                    t.kind,
                    t.version,
                    t.owner,
                    t.path,
                    json.dumps(t.meta, sort_keys=True),
                ]
                for t in sorted(rec.inputs.values(), key=lambda x: x.input_id)
            ],
        )
        self.con.executemany(
            "INSERT INTO elements VALUES (?, ?, ?, ?)",
            [
                [
                    run_id,
                    e.element_id,
                    e.kind,
                    json.dumps(
                        {k: f.model_dump(mode="json") for k, f in e.facets.items()}, sort_keys=True
                    ),
                ]
                for e in sorted(rec.elements.values(), key=lambda x: x.element_id)
            ],
        )
        self.con.executemany(
            "INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                [
                    run_id,
                    r.test_id,
                    r.agent,
                    r.kind,
                    r.status,
                    r.mode,
                    json.dumps(r.checks, sort_keys=True),
                    json.dumps(r.details),
                    json.dumps(r.tags),
                    json.dumps(r.rule_ids),
                    r.test_hash,
                    json.dumps(r.answer, sort_keys=True),
                    r.gt_hash,
                    r.baseline_gt_hash,
                    json.dumps(r.deps),
                    r.question,
                ]
                for r in rec.results
            ],
        )
        return info

    def run_ids(self) -> list[str]:
        return [r[0] for r in self.con.execute("SELECT run_id FROM runs ORDER BY seq").fetchall()]

    def load(self, run_id: str) -> RunRecord:
        cols = [d[0] for d in self.con.execute("SELECT * FROM runs LIMIT 0").description]
        row = self.con.execute("SELECT * FROM runs WHERE run_id = ?", [run_id]).fetchone()
        if row is None:
            raise KeyError(
                f"unknown run {run_id!r}; known runs: {', '.join(self.run_ids()) or 'none'}"
            )
        d: dict[str, Any] = dict(zip(cols, row, strict=True))
        d["agents"] = json.loads(d["agents"])
        info = RunInfo.model_validate(d)
        inputs = {
            r[0]: TrackedInput(
                input_id=r[0], kind=r[1], version=r[2], owner=r[3], path=r[4], meta=json.loads(r[5])
            )
            for r in self.con.execute(
                "SELECT input_id, kind, version, owner, path, meta FROM inputs WHERE run_id = ?",
                [run_id],
            ).fetchall()
        }
        elements = {
            r[0]: Element.model_validate(
                {"element_id": r[0], "kind": r[1], "facets": json.loads(r[2])}
            )
            for r in self.con.execute(
                "SELECT element_id, kind, facets FROM elements WHERE run_id = ?", [run_id]
            ).fetchall()
        }
        results = [
            TestResult(
                test_id=r[1],
                agent=r[2],
                kind=r[3],
                status=r[4],
                mode=r[5],
                checks=json.loads(r[6]),
                details=json.loads(r[7]),
                tags=json.loads(r[8]),
                rule_ids=json.loads(r[9]),
                test_hash=r[10],
                answer=json.loads(r[11]),
                gt_hash=r[12],
                baseline_gt_hash=r[13],
                deps=json.loads(r[14]),
                question=r[15] or "",
            )
            for r in self.con.execute(
                "SELECT * FROM results WHERE run_id = ? ORDER BY agent, test_id", [run_id]
            ).fetchall()
        ]
        return RunRecord(info=info, inputs=inputs, elements=elements, results=results)
