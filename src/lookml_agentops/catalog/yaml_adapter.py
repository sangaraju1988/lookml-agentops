"""Glossary stored as YAML in the repo (the default, works offline)."""

from __future__ import annotations

import re
from pathlib import Path

from lookml_agentops._util.io import load_yaml
from lookml_agentops.catalog.base import Catalog, CatalogAdapter, GlossaryTerm


class YamlCatalogAdapter(CatalogAdapter):
    """Reads ``{version: 1, terms: [...]}`` from a YAML file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Catalog:
        data = load_yaml(self.path) or {}
        terms = [GlossaryTerm.model_validate(t) for t in data.get("terms", [])]
        ids = [t.id for t in terms]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"{self.path}: duplicate glossary term ids {dupes}")
        lines: dict[str, int] = {}
        rx = re.compile(r"^\s*-?\s*id:\s*['\"]?([\w.:-]+)")
        for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            m = rx.match(line)
            if m and m.group(1) not in lines:
                lines[m.group(1)] = n
        cat = Catalog(source=str(self.path), terms=sorted(terms, key=lambda t: t.id))
        cat.lines = lines
        return cat
