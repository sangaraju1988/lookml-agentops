"""``owners.yaml``: CODEOWNERS-style mapping from input ids (and path globs) to teams.

```yaml
owners:
  - match: "lookml:core_project/**"   # input glob, optional "/<path glob>" inside the input
    team: central-data-platform
  - match: "runner:*"
    team: bi-platform
```

The most specific matching rule wins (a path glob beats none; then more literal characters);
ties go to the rule listed last. Unmatched inputs are owned by ``unassigned``.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lookml_agentops._util.io import load_yaml

UNASSIGNED = "unassigned"


class OwnerRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match: str
    team: str


class OwnersFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owners: list[OwnerRule]


def _path_regex(glob: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


@dataclass(frozen=True)
class _Compiled:
    index: int
    team: str
    input_glob: str
    path_glob: str | None
    specificity: tuple[int, int]

    def matches(self, iid: str, path: str | None) -> bool:
        if not fnmatch.fnmatchcase(iid, self.input_glob):
            return False
        if self.path_glob is None or self.path_glob == "**":
            return True
        return path is not None and bool(_path_regex(self.path_glob).match(path))


class Owners:
    def __init__(self, rules: list[OwnerRule]) -> None:
        compiled = []
        for i, r in enumerate(rules):
            kind_name, sep, rest = r.match.partition("/")
            path_glob = rest if sep else None
            literal = len(re.sub(r"[*?\[\]]", "", r.match))
            has_path = 1 if path_glob not in (None, "**") else 0
            compiled.append(_Compiled(i, r.team, kind_name, path_glob, (has_path, literal)))
        self._rules = compiled

    @classmethod
    def load(cls, path: Path | None) -> Owners:
        if path is None or not path.exists():
            return cls([])
        return cls(OwnersFile.model_validate(load_yaml(path) or {"owners": []}).owners)

    def owner(self, iid: str, path: str | None = None) -> str:
        best: _Compiled | None = None
        for r in self._rules:
            if r.matches(iid, path) and (
                best is None or (r.specificity, r.index) >= (best.specificity, best.index)
            ):
                best = r
        return best.team if best else UNASSIGNED

    def teams(self) -> list[str]:
        return sorted({r.team for r in self._rules})
