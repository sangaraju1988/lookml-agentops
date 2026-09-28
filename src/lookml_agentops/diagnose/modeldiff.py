"""Element-level diff between two runs: which facet of which element changed, where, by whom."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from lookml_agentops.diagnose.elements import Element


class ParamChange(BaseModel):
    param: str
    old: Any = None
    new: Any = None
    location: str | None = None  # "project/file:line" (LookML) or "file:line" (specs, catalog)


class ElementChange(BaseModel):
    element_id: str
    facet: str
    input_id: str
    location: str | None = None
    change: str  # added | removed | modified
    params: list[ParamChange] = Field(default_factory=list)

    def describe(self) -> str:
        what = ", ".join(p.param for p in self.params) or self.facet
        where = f" at {self.location}" if self.location else ""
        return f"{self.element_id} {what} {self.change}{where}"


def _input_for(loc: str | None, default: str) -> str:
    """LookML param locations look like 'project/file:line' -> lookml:project."""
    if default.startswith("lookml:") and loc and "/" in loc:
        return "lookml:" + loc.split("/", 1)[0]
    return default


def diff_element(old: Element | None, new: Element | None) -> list[ElementChange]:
    out: list[ElementChange] = []
    if old is None and new is None:
        return out
    ref = new or old
    assert ref is not None
    for facet in sorted(set(old.facets if old else {}) | set(new.facets if new else {})):
        a = old.facets.get(facet) if old else None
        b = new.facets.get(facet) if new else None
        if a is not None and b is not None and a.version == b.version:
            continue
        if a is None or b is None:
            f = b or a
            assert f is not None
            out.append(
                ElementChange(
                    element_id=ref.element_id,
                    facet=facet,
                    input_id=f.input_id,
                    location=f.location,
                    change="added" if a is None else "removed",
                )
            )
            continue
        params = []
        for p in sorted(set(a.params) | set(b.params)):
            pa, pb = a.params.get(p), b.params.get(p)
            if (pa or {}).get("v") != (pb or {}).get("v"):
                params.append(
                    ParamChange(
                        param=p,
                        old=(pa or {}).get("v"),
                        new=(pb or {}).get("v"),
                        location=(pb or pa or {}).get("loc"),
                    )
                )
        lead = params[0].location if params else b.location
        out.append(
            ElementChange(
                element_id=ref.element_id,
                facet=facet,
                input_id=_input_for(lead, b.input_id),
                location=lead if lead else b.location,
                change="modified",
                params=params,
            )
        )
    return out


def diff_elements(
    a: dict[str, Element], b: dict[str, Element], only: set[str] | None = None
) -> list[ElementChange]:
    keys = sorted((set(a) | set(b)) if only is None else only)
    out: list[ElementChange] = []
    for k in keys:
        out += diff_element(a.get(k), b.get(k))
    return out
