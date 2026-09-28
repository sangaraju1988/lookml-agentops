"""Canonical serialization and hashing helpers.

Everything that ends up in a diffed artifact goes through :func:`canonical_json` so that the
same inputs always produce byte-identical output.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_json(obj: Any, *, indent: int | None = 2) -> str:
    """Serialize ``obj`` as JSON with sorted keys, LF endings and a trailing newline."""
    text = json.dumps(obj, sort_keys=True, indent=indent, ensure_ascii=False, separators=None)
    return text + "\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_obj(obj: Any) -> str:
    """Hash of the compact canonical JSON form of ``obj``."""
    return sha256_text(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash(root: Path, *, suffixes: tuple[str, ...] | None = None) -> str:
    """Content hash of every file below ``root`` (relative path + bytes), order-independent."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if suffixes is not None and path.suffix not in suffixes:
            continue
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode("utf-8") + b"\0")
        h.update(sha256_file(path).encode("ascii") + b"\n")
    return h.hexdigest()
