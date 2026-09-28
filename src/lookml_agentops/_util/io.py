"""Stable file writing: UTF-8, LF line endings, only rewrite when content changes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from lookml_agentops._util.hashing import canonical_json


def write_text(path: Path, text: str) -> bool:
    """Write ``text`` to ``path``; return True if the file changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return False
    path.write_bytes(data)
    return True


def write_json(path: Path, obj: Any) -> bool:
    return write_text(path, canonical_json(obj))


def dump_yaml(obj: Any) -> str:
    return yaml.safe_dump(
        obj, sort_keys=True, allow_unicode=True, default_flow_style=False, width=100
    )


def write_yaml(path: Path, obj: Any) -> bool:
    return write_text(path, dump_yaml(obj))


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)
