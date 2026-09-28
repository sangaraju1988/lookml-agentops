"""Internal representation of LookML objects with provenance.

Only what lookml-agentops needs is modelled explicitly; every parameter is still kept in
``params`` so rules can inspect anything.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Literal

FieldKind = Literal["dimension", "dimension_group", "measure", "filter", "parameter"]
FIELD_KINDS: tuple[str, ...] = ("dimension", "dimension_group", "measure", "filter", "parameter")
ROOT_PROJECT = "@root"  # pseudo-project: file path relative to the config root (catalog, specs)
ProvOp = Literal["define", "refine", "extend", "override"]


@dataclass(frozen=True, order=True)
class Loc:
    project: str
    file: str
    line: int

    def __str__(self) -> str:
        if self.project == ROOT_PROJECT:
            return f"{self.file}:{self.line}"
        return f"{self.project}/{self.file}:{self.line}"


@dataclass(frozen=True)
class Prov:
    op: ProvOp
    loc: Loc

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "project": self.loc.project,
            "file": self.loc.file,
            "line": self.loc.line,
        }


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [str(v)]


def _yes(v: Any) -> bool:
    return str(v).strip().lower() == "yes"


@dataclass
class LField:
    view: str
    name: str
    kind: str
    params: dict[str, Any]
    provenance: list[Prov]
    exemptions: set[str] = field(default_factory=set)
    # where each parameter was last set (define / refine / override), for field-level attribution
    param_locs: dict[str, Loc] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.view}.{self.name}"

    @property
    def type(self) -> str:
        default = (
            "string"
            if self.kind == "dimension"
            else ("time" if self.kind == "dimension_group" else "")
        )
        return str(self.params.get("type", default))

    @property
    def sql(self) -> str | None:
        v = self.params.get("sql")
        return None if v is None else str(v)

    @property
    def label(self) -> str | None:
        v = self.params.get("label")
        return None if v is None else str(v)

    @property
    def description(self) -> str | None:
        v = self.params.get("description")
        return None if v is None else str(v)

    @property
    def hidden(self) -> bool:
        return _yes(self.params.get("hidden"))

    @property
    def tags(self) -> list[str]:
        return _as_list(self.params.get("tags"))

    @property
    def required_access_grants(self) -> list[str]:
        return _as_list(self.params.get("required_access_grants"))

    @property
    def timeframes(self) -> list[str]:
        return _as_list(self.params.get("timeframes"))

    @property
    def value_format_name(self) -> str | None:
        v = self.params.get("value_format_name")
        return None if v is None else str(v)

    @property
    def primary_key(self) -> bool:
        return _yes(self.params.get("primary_key"))

    @property
    def defined_at(self) -> Loc:
        return self.provenance[0].loc

    @property
    def last_at(self) -> Loc:
        return self.provenance[-1].loc

    @property
    def origin_project(self) -> str:
        return self.provenance[0].loc.project

    def has_tag(self, tag: str) -> bool:
        return tag in self.tags

    def glossary_ids(self) -> list[str]:
        return sorted(t.split(":", 1)[1] for t in self.tags if t.startswith("glossary:"))

    def display_label(self) -> str:
        return self.label or self.name.replace("_", " ").title()

    def clone(self) -> LField:
        return LField(
            self.view,
            self.name,
            self.kind,
            copy.deepcopy(self.params),
            list(self.provenance),
            set(self.exemptions),
            dict(self.param_locs),
        )


@dataclass
class LView:
    name: str
    params: dict[str, Any]
    fields: dict[str, LField]
    provenance: list[Prov]
    exemptions: set[str] = field(default_factory=set)

    @property
    def extends(self) -> list[str]:
        return _as_list(self.params.get("extends"))

    @property
    def sql_table_name(self) -> str | None:
        v = self.params.get("sql_table_name")
        return None if v is None else str(v)

    @property
    def derived_sql(self) -> str | None:
        dt = self.params.get("derived_table")
        if isinstance(dt, dict) and "sql" in dt:
            return str(dt["sql"])
        return None

    @property
    def extension_required(self) -> bool:
        return str(self.params.get("extension", "")) == "required"

    @property
    def tags(self) -> list[str]:
        return _as_list(self.params.get("tags"))

    def clone(self) -> LView:
        return LView(
            self.name,
            copy.deepcopy(self.params),
            {k: f.clone() for k, f in self.fields.items()},
            list(self.provenance),
            set(self.exemptions),
        )


@dataclass
class LJoin:
    name: str
    params: dict[str, Any]
    provenance: list[Prov]

    @property
    def from_view(self) -> str:
        return str(self.params.get("from", self.name))

    @property
    def sql_on(self) -> str | None:
        v = self.params.get("sql_on")
        return None if v is None else str(v)

    @property
    def relationship(self) -> str:
        return str(self.params.get("relationship", "many_to_one"))

    @property
    def join_type(self) -> str:
        return str(self.params.get("type", "left_outer"))

    def clone(self) -> LJoin:
        return LJoin(self.name, copy.deepcopy(self.params), list(self.provenance))


@dataclass
class LExplore:
    name: str
    params: dict[str, Any]
    joins: dict[str, LJoin]
    provenance: list[Prov]
    exemptions: set[str] = field(default_factory=set)
    param_locs: dict[str, Loc] = field(default_factory=dict)

    @property
    def base_view(self) -> str:
        return str(self.params.get("from", self.params.get("view_name", self.name)))

    @property
    def base_alias(self) -> str:
        return (
            str(self.params.get("view_name", self.name)) if "from" not in self.params else self.name
        )

    @property
    def extends(self) -> list[str]:
        return _as_list(self.params.get("extends"))

    @property
    def extension_required(self) -> bool:
        return str(self.params.get("extension", "")) == "required"

    @property
    def hidden(self) -> bool:
        return _yes(self.params.get("hidden"))

    @property
    def sql_always_where(self) -> str | None:
        v = self.params.get("sql_always_where")
        return None if v is None else str(v)

    @property
    def always_filter(self) -> dict[str, str]:
        af = self.params.get("always_filter")
        out: dict[str, str] = {}
        if isinstance(af, dict):
            for item in af.get("filters", []) or []:
                if isinstance(item, dict):
                    out.update({str(k): str(v) for k, v in item.items()})
        return out

    @property
    def tags(self) -> list[str]:
        return _as_list(self.params.get("tags"))

    @property
    def label(self) -> str | None:
        v = self.params.get("label")
        return None if v is None else str(v)

    @property
    def description(self) -> str | None:
        v = self.params.get("description")
        return None if v is None else str(v)

    def aliases(self) -> dict[str, str]:
        """alias -> underlying view name, base first then joins in declaration order."""
        out = {self.base_alias: self.base_view}
        for j in self.joins.values():
            out[j.name] = j.from_view
        return out

    def clone(self) -> LExplore:
        return LExplore(
            self.name,
            copy.deepcopy(self.params),
            {k: j.clone() for k, j in self.joins.items()},
            list(self.provenance),
            set(self.exemptions),
            dict(self.param_locs),
        )


@dataclass
class AccessGrant:
    name: str
    params: dict[str, Any]
    loc: Loc


TIMEFRAME_DEFAULTS = ["raw", "time", "date", "week", "month", "quarter", "year"]


def expand_field_names(f: LField) -> list[tuple[str, str | None]]:
    """Queryable names for a field: ``[(name, timeframe)]``."""
    if f.kind == "dimension_group":
        tfs = f.timeframes or TIMEFRAME_DEFAULTS
        return [(f"{f.name}_{tf}", tf) for tf in tfs]
    return [(f.name, None)]
