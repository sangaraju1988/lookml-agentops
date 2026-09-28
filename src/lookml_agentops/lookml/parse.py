"""Parse ``.lkml`` files into generic dicts while keeping line numbers.

``lkml.load`` drops positions, so we walk the syntax tree from ``lkml.parse`` instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lkml
from lkml import tree

from lookml_agentops.lookml.model import (
    FIELD_KINDS,
    AccessGrant,
    LExplore,
    LField,
    LJoin,
    Loc,
    LView,
    Prov,
)

EXEMPT_RE = re.compile(r"#\s*lkagent:disable\s+([A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*)(.*)$")


@dataclass
class Exemption:
    rules: set[str]
    reason: str | None
    comment_line: int


@dataclass
class ParsedFile:
    project: str
    path: str  # relative to project root, posix
    includes: list[tuple[str, int]] = field(default_factory=list)
    views: list[LView] = field(default_factory=list)
    explores: list[LExplore] = field(default_factory=list)
    access_grants: list[AccessGrant] = field(default_factory=list)
    top: dict[str, Any] = field(default_factory=dict)
    local_dependencies: list[str] = field(default_factory=list)
    remote_dependencies: list[dict[str, Any]] = field(default_factory=list)
    exemptions: dict[int, Exemption] = field(default_factory=dict)  # object line -> exemption
    exemption_problems: list[tuple[int, str]] = field(default_factory=list)

    @property
    def kind(self) -> str:
        for k in ("model", "view", "explore", "manifest"):
            if self.path.endswith(f".{k}.lkml") or (
                k == "manifest" and self.path.endswith("manifest.lkml")
            ):
                return k
        return "other"


def _token_value(tok: Any) -> str:
    return str(tok.value)


def _node_value(node: Any) -> Any:
    if isinstance(node, tree.PairNode):
        return _token_value(node.value)
    if isinstance(node, tree.ListNode):
        items: list[Any] = []
        for it in node.items:
            if isinstance(it, tree.PairNode):
                items.append({_token_value(it.type): _token_value(it.value)})
            elif isinstance(it, tree.BlockNode):
                items.append(_block_dict(it))
            else:
                items.append(_token_value(it))
        return items
    if isinstance(node, tree.BlockNode):
        return _block_dict(node)
    raise TypeError(f"unexpected node {type(node).__name__}")


def _block_dict(block: tree.BlockNode) -> dict[str, Any]:
    out: dict[str, Any] = {}
    repeated: set[str] = set()
    if block.name is not None:
        out["name"] = _token_value(block.name)
    for child in block.container.items:
        key = _token_value(child.type)
        val = _node_value(child)
        if key in repeated:
            out[key].append(val)
        elif key in out:
            # repeated keys (e.g. several link blocks) accumulate into a list
            out[key] = [out[key], val]
            repeated.add(key)
        else:
            out[key] = val
    return out


def _line(block: Any) -> int:
    return int(block.type.line_number)


def _parse_view(block: tree.BlockNode, project: str, path: str) -> LView:
    name = _token_value(block.name)
    vloc = Loc(project, path, _line(block))
    params: dict[str, Any] = {}
    fields: dict[str, LField] = {}
    for child in block.container.items:
        key = _token_value(child.type)
        if key in FIELD_KINDS and isinstance(child, tree.BlockNode):
            fname = _token_value(child.name)
            fp = _block_dict(child)
            fp.pop("name", None)
            fields[fname] = LField(
                view=name.lstrip("+"),
                name=fname,
                kind=key,
                params=fp,
                provenance=[Prov("define", Loc(project, path, _line(child)))],
            )
        else:
            params[key] = _node_value(child)
    return LView(name=name, params=params, fields=fields, provenance=[Prov("define", vloc)])


def _parse_explore(block: tree.BlockNode, project: str, path: str) -> LExplore:
    name = _token_value(block.name)
    params: dict[str, Any] = {}
    joins: dict[str, LJoin] = {}
    for child in block.container.items:
        key = _token_value(child.type)
        if key == "join" and isinstance(child, tree.BlockNode):
            jp = _block_dict(child)
            jname = str(jp.pop("name"))
            joins[jname] = LJoin(jname, jp, [Prov("define", Loc(project, path, _line(child)))])
        else:
            params[key] = _node_value(child)
    return LExplore(name, params, joins, [Prov("define", Loc(project, path, _line(block)))])


def _scan_exemptions(text: str, pf: ParsedFile, object_lines: list[int]) -> None:
    """``# lkagent:disable LKA001[,LKA002] reason="..."`` applies to the object on the same
    line or the next object line below the comment."""
    lines = text.splitlines()
    ordered = sorted(set(object_lines))
    for i, raw in enumerate(lines, start=1):
        m = EXEMPT_RE.search(raw)
        if not m:
            continue
        rules = {r.strip() for r in m.group(1).split(",")}
        rm = re.search(r'reason\s*=\s*"([^"]*)"', m.group(2))
        reason = rm.group(1) if rm and rm.group(1).strip() else None
        before = raw[: m.start()].strip()
        target = i if before else next((ln for ln in ordered if ln > i), None)
        if target is None:
            pf.exemption_problems.append((i, "exemption comment does not precede any object"))
            continue
        pf.exemptions[target] = Exemption(rules, reason, i)
        if reason is None:
            pf.exemption_problems.append((i, "exemption has no reason"))


def parse_file(root: Path, rel: str, project: str) -> ParsedFile:
    text = (root / rel).read_text(encoding="utf-8")
    pf = ParsedFile(project=project, path=rel)
    doc = lkml.parse(text)
    object_lines: list[int] = []
    for node in doc.container.items:
        key = _token_value(node.type)
        if key == "view" and isinstance(node, tree.BlockNode):
            v = _parse_view(node, project, rel)
            pf.views.append(v)
            object_lines.append(v.provenance[0].loc.line)
            object_lines.extend(f.provenance[0].loc.line for f in v.fields.values())
        elif key == "explore" and isinstance(node, tree.BlockNode):
            e = _parse_explore(node, project, rel)
            pf.explores.append(e)
            object_lines.append(e.provenance[0].loc.line)
            object_lines.extend(j.provenance[0].loc.line for j in e.joins.values())
        elif key == "access_grant" and isinstance(node, tree.BlockNode):
            d = _block_dict(node)
            pf.access_grants.append(
                AccessGrant(str(d.pop("name")), d, Loc(project, rel, _line(node)))
            )
        elif key == "include":
            pf.includes.append((str(_node_value(node)), _line(node)))
        elif key == "local_dependency" and isinstance(node, tree.BlockNode):
            pf.local_dependencies.append(str(_block_dict(node).get("project")))
        elif key == "remote_dependency" and isinstance(node, tree.BlockNode):
            pf.remote_dependencies.append(_block_dict(node))
        else:
            pf.top[key] = _node_value(node)
    _scan_exemptions(text, pf, object_lines)
    _apply_exemptions(pf)
    return pf


def _apply_exemptions(pf: ParsedFile) -> None:
    for v in pf.views:
        if (ex := pf.exemptions.get(v.provenance[0].loc.line)) is not None:
            v.exemptions |= ex.rules
        for f in v.fields.values():
            if (ex := pf.exemptions.get(f.provenance[0].loc.line)) is not None:
                f.exemptions |= ex.rules
    for e in pf.explores:
        if (ex := pf.exemptions.get(e.provenance[0].loc.line)) is not None:
            e.exemptions |= ex.rules
