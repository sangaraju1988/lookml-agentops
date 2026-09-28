"""Resolve a Looker project into its *effective model*.

Semantics follow the Looker docs (see docs/api-verification.md):

* ``include:`` paths starting with ``//<project>/`` refer to imported projects; ``/`` is the
  project root; other paths are relative to the including file. ``*.view`` implies ``.lkml``.
* Refinements (``view: +x`` / ``explore: +x``) are applied in include order (later wins; within a
  file, lower lines win). ``join``, ``link``, ``filters`` are additive; ``extends`` in a
  refinement is appended.
* ``extends`` values are applied first, then the object's own values, then its refinements.
  Because a refined object already holds "own + refinements", we refine first and then layer the
  refined child over its (refined, resolved) parents.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lookml_agentops.config import LkagentConfig
from lookml_agentops.lookml.model import AccessGrant, LExplore, LField, LJoin, Loc, LView, Prov
from lookml_agentops.lookml.parse import ParsedFile, parse_file

ADDITIVE_FIELD_PARAMS = ("link", "filters", "action")


class ResolveError(Exception):
    pass


@dataclass
class Project:
    name: str
    root: Path
    files: dict[str, ParsedFile]

    @property
    def manifest(self) -> ParsedFile | None:
        return self.files.get("manifest.lkml")

    @property
    def dependencies(self) -> list[str]:
        m = self.manifest
        if m is None:
            return []
        remote = [str(d.get("name")) for d in m.remote_dependencies]
        return sorted({*m.local_dependencies, *remote})

    @property
    def model_files(self) -> list[str]:
        return sorted(p for p in self.files if p.endswith(".model.lkml"))


@dataclass
class Workspace:
    projects: dict[str, Project]


def load_project(name: str, root: Path) -> Project:
    if not root.is_dir():
        raise ResolveError(f"project {name}: directory not found: {root}")
    files: dict[str, ParsedFile] = {}
    for path in sorted(root.rglob("*.lkml")):
        rel = path.relative_to(root).as_posix()
        files[rel] = parse_file(root, rel, name)
    return Project(name=name, root=root, files=files)


def load_workspace(cfg: LkagentConfig) -> Workspace:
    return Workspace({n: load_project(n, cfg.project_path(n)) for n in sorted(cfg.projects)})


def _glob_regex(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


@dataclass
class EffectiveModel:
    project: str
    model_files: list[str]
    included: list[tuple[str, str]]
    views: dict[str, LView]
    explores: dict[str, LExplore]
    access_grants: dict[str, AccessGrant]
    imports: list[str]
    problems: list[str] = field(default_factory=list)
    explore_defs: dict[str, Loc] = field(default_factory=dict)  # base definition location

    def field(self, view: str, name: str) -> LField | None:
        v = self.views.get(view)
        return None if v is None else v.fields.get(name)

    def queryable_explores(self) -> list[LExplore]:
        return [e for _, e in sorted(self.explores.items()) if not e.extension_required]

    def ancestors(self, name: str) -> list[str]:
        """``name`` followed by every explore it extends (depth-first, declaration order)."""
        out = [name]
        e = self.explores.get(name)
        for parent in e.extends if e is not None else []:
            for a in self.ancestors(parent):
                if a not in out:
                    out.append(a)
        return out

    def explore_views(self, explore: LExplore) -> dict[str, LView]:
        """alias -> view for an explore (skips aliases whose view is missing)."""
        return {a: self.views[v] for a, v in explore.aliases().items() if v in self.views}


class _Resolver:
    def __init__(self, ws: Workspace, project: str) -> None:
        self.ws = ws
        self.project = ws.projects[project]
        self.problems: list[str] = []

    def expand_include(self, proj: str, current: str, pattern: str) -> list[tuple[str, str]]:
        if pattern.startswith("//"):
            target, _, rest = pattern[2:].partition("/")
            if target != self.project.name and target not in self.project.dependencies:
                self.problems.append(
                    f"{proj}/{current}: include {pattern!r} references project {target!r} "
                    "that is not declared in manifest.lkml"
                )
            rel_pattern = rest
        elif pattern.startswith("/"):
            target, rel_pattern = proj, pattern[1:]
        else:
            target = proj
            rel_pattern = posixpath.normpath(posixpath.join(posixpath.dirname(current), pattern))
        if not rel_pattern.endswith(".lkml"):
            rel_pattern += ".lkml"
        tp = self.ws.projects.get(target)
        if tp is None:
            self.problems.append(f"{proj}/{current}: unknown project in include {pattern!r}")
            return []
        rx = _glob_regex(rel_pattern)
        matches = [(target, rel) for rel in sorted(tp.files) if rx.match(rel)]
        if not matches:
            self.problems.append(f"{proj}/{current}: include {pattern!r} matched no files")
        return matches

    def include_order(self) -> list[tuple[str, str]]:
        order: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def visit(proj: str, rel: str) -> None:
            if (proj, rel) in seen:
                return
            seen.add((proj, rel))
            order.append((proj, rel))
            for pattern, _line in self.ws.projects[proj].files[rel].includes:
                for m in self.expand_include(proj, rel, pattern):
                    visit(*m)

        for mf in self.project.model_files:
            visit(self.project.name, mf)
        return order

    def resolve(self) -> EffectiveModel:
        if not self.project.model_files:
            raise ResolveError(f"project {self.project.name} has no .model.lkml file")
        order = self.include_order()
        base_views: dict[str, LView] = {}
        base_explores: dict[str, LExplore] = {}
        view_refs: dict[str, list[LView]] = {}
        explore_refs: dict[str, list[LExplore]] = {}
        grants: dict[str, AccessGrant] = {}
        explore_defs: dict[str, Loc] = {}
        for proj, rel in order:
            pf = self.ws.projects[proj].files[rel]
            for v in pf.views:
                if v.name.startswith("+"):
                    view_refs.setdefault(v.name[1:], []).append(v)
                elif v.name in base_views:
                    self.problems.append(f"view {v.name!r} defined twice ({v.provenance[0].loc})")
                else:
                    base_views[v.name] = v.clone()
            for e in pf.explores:
                if e.name.startswith("+"):
                    explore_refs.setdefault(e.name[1:], []).append(e)
                elif e.name in base_explores:
                    self.problems.append(
                        f"explore {e.name!r} defined twice ({e.provenance[0].loc})"
                    )
                else:
                    base_explores[e.name] = e.clone()
                    explore_defs[e.name] = e.provenance[0].loc
            if pf.kind == "model" and proj == self.project.name:
                for g in pf.access_grants:
                    grants[g.name] = g

        for name, refs in view_refs.items():
            if name not in base_views:
                self.problems.append(
                    f"refinement of unknown view {name!r} ({refs[0].provenance[0].loc})"
                )
                continue
            for r in refs:
                _refine_view(base_views[name], r)
        for name, erefs in explore_refs.items():
            if name not in base_explores:
                self.problems.append(
                    f"refinement of unknown explore {name!r} ({erefs[0].provenance[0].loc})"
                )
                continue
            for er in erefs:
                _refine_explore(base_explores[name], er)

        views: dict[str, LView] = {}
        explores: dict[str, LExplore] = {}
        for name in sorted(base_views):
            views[name] = self._extend_view(name, base_views, views, [])
        for name in sorted(base_explores):
            explores[name] = self._extend_explore(name, base_explores, explores, [])

        return EffectiveModel(
            project=self.project.name,
            model_files=self.project.model_files,
            included=order,
            views=views,
            explores=explores,
            access_grants=grants,
            imports=self.project.dependencies,
            problems=self.problems,
            explore_defs=explore_defs,
        )

    def _extend_view(
        self, name: str, base: dict[str, LView], done: dict[str, LView], stack: list[str]
    ) -> LView:
        if name in done:
            return done[name]
        if name in stack:
            raise ResolveError(f"extends cycle: {' -> '.join([*stack, name])}")
        child = base[name]
        if not child.extends:
            done[name] = child.clone()
            return done[name]
        merged: LView | None = None
        for parent in child.extends:
            if parent not in base:
                self.problems.append(f"view {name!r} extends unknown view {parent!r}")
                continue
            p = self._extend_view(parent, base, done, [*stack, name])
            merged = p.clone() if merged is None else _overlay_view(merged, p, "extend")
        if merged is None:
            done[name] = child.clone()
            return done[name]
        out = merged.clone()
        out.name = name
        out.params.pop("extension", None)
        loc = child.provenance[0].loc
        for f in out.fields.values():
            f.view = name
            f.provenance.append(Prov("extend", loc))
        out = _overlay_view(out, child, "override")
        out.provenance = list(child.provenance)
        out.exemptions = set(child.exemptions)
        done[name] = out
        return out

    def _extend_explore(
        self, name: str, base: dict[str, LExplore], done: dict[str, LExplore], stack: list[str]
    ) -> LExplore:
        if name in done:
            return done[name]
        if name in stack:
            raise ResolveError(f"extends cycle: {' -> '.join([*stack, name])}")
        child = base[name]
        if not child.extends:
            done[name] = child.clone()
            return done[name]
        merged: LExplore | None = None
        for parent in child.extends:
            if parent not in base:
                self.problems.append(f"explore {name!r} extends unknown explore {parent!r}")
                continue
            p = self._extend_explore(parent, base, done, [*stack, name])
            merged = p.clone() if merged is None else _overlay_explore(merged, p)
        if merged is None:
            done[name] = child.clone()
            return done[name]
        out = merged.clone()
        out.name = name
        out.params.pop("extension", None)
        out = _overlay_explore(out, child)
        out.provenance = [*merged.provenance, *child.provenance]
        out.exemptions = set(child.exemptions) | set(merged.exemptions)
        done[name] = out
        return out


def _merge_field_params(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for k, v in src.items():
        if k in ADDITIVE_FIELD_PARAMS and k in dst:
            a = dst[k] if isinstance(dst[k], list) else [dst[k]]
            b = v if isinstance(v, list) else [v]
            dst[k] = [*a, *b]
        else:
            dst[k] = v


def _refine_view(base: LView, ref: LView) -> None:
    loc = ref.provenance[0].loc
    base.provenance.append(Prov("refine", loc))
    for k, v in ref.params.items():
        if k == "extends":
            base.params["extends"] = [*base.extends, *(v if isinstance(v, list) else [v])]
        else:
            base.params[k] = v
    for fname, rf in ref.fields.items():
        if fname in base.fields:
            bf = base.fields[fname]
            _merge_field_params(bf.params, rf.params)
            bf.provenance.append(Prov("refine", rf.provenance[0].loc))
            bf.exemptions |= rf.exemptions
        else:
            nf = rf.clone()
            nf.view = base.name
            base.fields[fname] = nf
    base.exemptions |= ref.exemptions


def _overlay_view(dst: LView, src: LView, op: str) -> LView:
    out = dst.clone()
    for k, v in src.params.items():
        out.params[k] = v
    for fname, sf in src.fields.items():
        if fname in out.fields:
            of = out.fields[fname]
            _merge_field_params(of.params, sf.params)
            of.provenance.append(
                Prov("override" if op == "override" else "extend", sf.provenance[-1].loc)
            )
            of.exemptions |= sf.exemptions
        else:
            nf = sf.clone()
            nf.view = out.name
            out.fields[fname] = nf
    return out


def _merge_joins(dst: dict[str, LJoin], src: dict[str, LJoin], op: str) -> None:
    for jname, sj in src.items():
        if jname in dst:
            dst[jname].params.update(sj.params)
            dst[jname].provenance.append(
                Prov("refine" if op == "refine" else "override", sj.provenance[0].loc)
            )
        else:
            dst[jname] = sj.clone()


def _refine_explore(base: LExplore, ref: LExplore) -> None:
    base.provenance.append(Prov("refine", ref.provenance[0].loc))
    for k, v in ref.params.items():
        if k == "extends":
            base.params["extends"] = [*base.extends, *(v if isinstance(v, list) else [v])]
        else:
            base.params[k] = v
    _merge_joins(base.joins, ref.joins, "refine")
    base.exemptions |= ref.exemptions


def _overlay_explore(dst: LExplore, src: LExplore) -> LExplore:
    out = dst.clone()
    for k, v in src.params.items():
        out.params[k] = v
    _merge_joins(out.joins, src.joins, "override")
    return out


def resolve_project(ws: Workspace, project: str) -> EffectiveModel:
    return _Resolver(ws, project).resolve()
