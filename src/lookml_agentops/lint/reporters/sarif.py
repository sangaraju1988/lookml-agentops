"""SARIF 2.1.0 output for GitHub code scanning."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from lookml_agentops import __version__
from lookml_agentops.config import LkagentConfig
from lookml_agentops.lint.engine import LintResult
from lookml_agentops.lookml.model import CATALOG_PROJECT, Loc

LEVEL = {"error": "error", "warning": "warning", "note": "note"}


def _uri(cfg: LkagentConfig, loc: Loc, base: Path) -> str:
    if loc.project == CATALOG_PROJECT:
        abs_path = cfg.root / loc.file
    else:
        abs_path = cfg.project_path(loc.project) / loc.file
    return Path(os.path.relpath(abs_path.resolve(), base.resolve())).as_posix()


def render_sarif(result: LintResult, cfg: LkagentConfig, base: Path) -> dict[str, Any]:
    rules = [
        {
            "id": r.id,
            "name": r.name,
            "shortDescription": {"text": r.name.replace("-", " ")},
            "fullDescription": {"text": r.rationale},
            "help": {"text": r.fix_hint},
            "defaultConfiguration": {"level": LEVEL[r.severity]},
        }
        for r in result.rules
    ]
    results = []
    for f in result.findings:
        res: dict[str, Any] = {
            "ruleId": f.rule_id,
            "level": LEVEL[f.severity],
            "message": {"text": f.message},
        }
        if f.loc is not None:
            res["locations"] = [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": _uri(cfg, f.loc, base)},
                        "region": {"startLine": max(1, f.loc.line)},
                    }
                }
            ]
        results.append(res)
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "lkagent",
                        "version": __version__,
                        "informationUri": "https://github.com/lookml-agentops/lookml-agentops",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
