"""Canonical CSV output: fixed column order, LF endings, fixed number/date formats."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from lookml_agentops.seed.generator import Table
from lookml_agentops.seed.schema import TABLES


def _fmt(value: Any, typ: str) -> str:
    if value is None:
        return ""
    if typ.startswith("DECIMAL"):
        cents = int(value)
        sign = "-" if cents < 0 else ""
        cents = abs(cents)
        return f"{sign}{cents // 100}.{cents % 100:02d}"
    if typ == "BOOLEAN":
        return "true" if value else "false"
    if typ == "TIMESTAMP":
        return dt.datetime.fromtimestamp(int(value), tz=dt.UTC).strftime("%Y-%m-%d %H:%M:%S")
    if typ == "DATE":
        if isinstance(value, str):
            return value
        return dt.datetime.fromtimestamp(int(value), tz=dt.UTC).strftime("%Y-%m-%d")
    text = str(value)
    if any(ch in text for ch in ',"\n'):
        return '"' + text.replace('"', '""') + '"'
    return text


def render_csv(name: str, table: Table) -> bytes:
    cols = TABLES[name]
    missing = {c for c, _ in cols} ^ set(table)
    if missing:
        raise ValueError(f"table {name}: column mismatch {sorted(missing)}")
    n = len(table[cols[0][0]])
    lines = [",".join(c for c, _ in cols)]
    columns = [(table[c], typ) for c, typ in cols]
    for i in range(n):
        lines.append(",".join(_fmt(col[i], typ) for col, typ in columns))
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_csvs(tables: dict[str, Table], out_dir: Path) -> dict[str, tuple[int, bytes]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered: dict[str, tuple[int, bytes]] = {}
    for name in sorted(TABLES):
        data = render_csv(name, tables[name])
        (out_dir / f"{name}.csv").write_bytes(data)
        rendered[name] = (len(tables[name][TABLES[name][0][0]]), data)
    return rendered
