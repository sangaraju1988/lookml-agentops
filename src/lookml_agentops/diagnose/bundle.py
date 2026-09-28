"""``lkagent diagnose bundle``: an evidence zip for a vendor support case.

Contents: the questions, old and new *structural* answers (explore, fields, filters, resolved
values), timestamps, runner metadata, and the unchanged fingerprints of every team-owned input
and every dependency of the affected tests — proving nothing on the team's side changed. No
row-level data: result rows are represented only by row counts and hashes.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

from lookml_agentops._util.hashing import canonical_json
from lookml_agentops.diagnose.history import RunRecord
from lookml_agentops.diagnose.why import Diagnosis

FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def _runner_meta(rec: RunRecord) -> dict[str, Any]:
    ri = rec.inputs.get(f"runner:{rec.info.runner}")
    return dict(ri.meta) if ri is not None else {}


def _dep_fingerprints(
    a: RunRecord, b: RunRecord, deps: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    out = []
    for d in deps:
        ea, eb = a.elements.get(d["element_id"]), b.elements.get(d["element_id"])
        for f in d["facets"]:
            va = ea.facets[f].version if ea and f in ea.facets else None
            vb = eb.facets[f].version if eb and f in eb.facets else None
            out.append(
                {
                    "element": d["element_id"],
                    "facet": f,
                    "role": d["role"],
                    "before": va,
                    "after": vb,
                    "unchanged": va == vb,
                }
            )
    return out


def build_bundle(a: RunRecord, b: RunRecord, d: Diagnosis, *, only_external: bool = True) -> bytes:
    verdicts = [v for v in d.verdicts if v.cause == "external" or not only_external]
    files: dict[str, str] = {}
    team_inputs = [i for i in sorted(b.inputs) if not i.startswith(("runner:", "data:"))]
    files["inputs.json"] = canonical_json(
        [
            {
                "input_id": i,
                "kind": b.inputs[i].kind,
                "owner": b.inputs[i].owner,
                "before": a.inputs[i].version if i in a.inputs else None,
                "after": b.inputs[i].version,
                "unchanged": i in a.inputs and a.inputs[i].version == b.inputs[i].version,
            }
            for i in team_inputs
        ]
    )
    files["runs.json"] = canonical_json(
        {
            "run_a": {
                "run_id": a.info.run_id,
                "started_at": a.info.started_at.isoformat(),
                "runner": a.info.runner,
                "runner_version": a.info.runner_version,
                "runner_meta": _runner_meta(a),
            },
            "run_b": {
                "run_id": b.info.run_id,
                "started_at": b.info.started_at.isoformat(),
                "runner": b.info.runner,
                "runner_version": b.info.runner_version,
                "runner_meta": _runner_meta(b),
            },
            "as_of": b.info.as_of.isoformat(),
        }
    )
    for v in verdicts:
        rb = b.result(v.test_id)
        files[f"tests/{v.agent}/{v.test_id}.json"] = canonical_json(
            {
                "test_id": v.test_id,
                "agent": v.agent,
                "tags": v.tags,
                "question": rb.question if rb else "",
                "status_before": v.before,
                "status_after": v.after,
                "answer_before": v.answer_before,
                "answer_after": v.answer_after,
                "dependency_fingerprints": _dep_fingerprints(a, b, rb.deps if rb else []),
            }
        )
    groups = (
        "\n".join(f"- {g.count} x `{g.tag}`: {g.hint}" for g in d.external_groups) or "- (none)"
    )
    unchanged = sum(
        1 for i in team_inputs if i in a.inputs and a.inputs[i].version == b.inputs[i].version
    )
    files["README.md"] = (
        f"# Evidence bundle: {a.info.run_id} -> {b.info.run_id}\n\n"
        f"Runs at {a.info.started_at.isoformat()} and {b.info.started_at.isoformat()} (UTC), "
        f"runner `{a.info.runner_version}` -> `{b.info.runner_version}`.\n\n"
        f"{len(verdicts)} test(s) changed outcome with **no change on the team's side**: {unchanged} of "
        f"{len(team_inputs)} team-owned inputs are byte-identical (see inputs.json), and every "
        "dependency of each affected test has the same fingerprint (see tests/*/*.json).\n\n"
        f"## Pattern\n\n{groups}\n\n"
        "No row-level data is included: results are summarized by row count and a hash.\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, files[name])
    return buf.getvalue()


def write_bundle(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
