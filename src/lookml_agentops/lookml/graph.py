"""Import graph and effective-model rendering (text, Mermaid, JSON)."""

from __future__ import annotations

from typing import Any

from lookml_agentops.lookml.model import expand_field_names
from lookml_agentops.lookml.resolve import EffectiveModel, Workspace


def import_edges(ws: Workspace) -> list[tuple[str, str, str]]:
    """``(importer, imported, kind)`` for every manifest dependency."""
    edges: list[tuple[str, str, str]] = []
    for name in sorted(ws.projects):
        m = ws.projects[name].manifest
        if m is None:
            continue
        edges += [(name, dep, "local_dependency") for dep in sorted(m.local_dependencies)]
        edges += [
            (name, str(d.get("name")), "remote_dependency")
            for d in sorted(m.remote_dependencies, key=lambda d: str(d.get("name")))
        ]
    return edges


def render_text(ws: Workspace) -> str:
    edges = import_edges(ws)
    imported = {b for _, b, _ in edges}
    lines: list[str] = []
    roots = [p for p in sorted(ws.projects) if p not in imported]
    by_src: dict[str, list[tuple[str, str]]] = {}
    for a, b, k in edges:
        by_src.setdefault(a, []).append((b, k))

    def walk(node: str, prefix: str, seen: frozenset[str]) -> None:
        children = by_src.get(node, [])
        for i, (child, kind) in enumerate(children):
            last = i == len(children) - 1
            lines.append(f"{prefix}{'└── ' if last else '├── '}{child} ({kind})")
            if child not in seen:
                walk(child, prefix + ("    " if last else "│   "), seen | {child})

    for r in roots:
        lines.append(r)
        walk(r, "", frozenset({r}))
    return "\n".join(lines) + "\n"


def render_mermaid(ws: Workspace) -> str:
    lines = ["graph LR"]
    for p in sorted(ws.projects):
        lines.append(f"  {p}[{p}]")
    for a, b, k in import_edges(ws):
        lines.append(f"  {a} -->|{k}| {b}")
    return "\n".join(lines) + "\n"


def effective_model_dict(em: EffectiveModel) -> dict[str, Any]:
    views: dict[str, Any] = {}
    for vname, v in sorted(em.views.items()):
        views[vname] = {
            "extension_required": v.extension_required,
            "provenance": [p.to_dict() for p in v.provenance],
            "fields": {
                fname: {
                    "kind": f.kind,
                    "type": f.type,
                    "hidden": f.hidden,
                    "tags": f.tags,
                    "provenance": [p.to_dict() for p in f.provenance],
                }
                for fname, f in sorted(v.fields.items())
            },
        }
    explores: dict[str, Any] = {}
    for ename, e in sorted(em.explores.items()):
        explores[ename] = {
            "base_view": e.base_view,
            "extension_required": e.extension_required,
            "joins": {j.name: j.from_view for j in e.joins.values()},
            "provenance": [p.to_dict() for p in e.provenance],
        }
    return {
        "project": em.project,
        "imports": em.imports,
        "included_files": [f"{p}/{f}" for p, f in em.included],
        "views": views,
        "explores": explores,
        "problems": em.problems,
    }


def render_fields_text(em: EffectiveModel) -> str:
    """One line per field: ``view.field  kind  defined-at [-> last change]``."""
    rows: list[str] = []
    for vname, v in sorted(em.views.items()):
        for fname, f in sorted(v.fields.items()):
            chain = " -> ".join(f"{p.op}@{p.loc}" for p in f.provenance)
            names = (
                ",".join(n for n, _ in expand_field_names(f)) if f.kind == "dimension_group" else ""
            )
            extra = f" [{names}]" if names else ""
            rows.append(f"{vname}.{fname:<28} {f.kind:<15} {chain}{extra}")
    return "\n".join(rows) + "\n"
