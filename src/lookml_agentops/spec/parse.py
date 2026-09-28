"""Parse a ``*.agent.md`` file: YAML frontmatter + Markdown sections by ``##`` heading.

```markdown
---
agent: finance-analyst
description: Answers FP&A questions.
extends: [shared/company-baseline.agent.md]
explores: [finance_project::finance_orders]
derive: {lookml_descriptions: true, catalog_glossary: true}
---
## Role            free text
## Audience        free text
## Rules           YAML list of {id, text, locked?}
## Vocabulary      lines: - "phrase"[, "phrase"...] → explore.field | view.field
## Guardrails      YAML list of strings or {id, text}
## Golden queries  YAML list of {id, questions, looker_query | explore_url}
```

Unknown sections or frontmatter keys are errors with a helpful message. Every item keeps its
line number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lookml_agentops.spec.errors import SpecError

SECTIONS = ["Role", "Audience", "Rules", "Vocabulary", "Guardrails", "Golden queries"]
_SECTION_KEYS = {s.lower(): s for s in SECTIONS}
VOCAB_RE = re.compile(r'^-\s+((?:"[^"]+"\s*,?\s*)+)\s*(?:→|->)\s*(\S+)\s*$')
AGENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class Derive(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lookml_descriptions: bool = False
    catalog_glossary: bool = False


class Frontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str
    description: str
    extends: list[str] = Field(default_factory=list)
    explores: list[str] = Field(default_factory=list)
    derive: Derive = Field(default_factory=Derive)


@dataclass
class RawRule:
    rule_id: str
    text: str
    locked: bool
    line: int


@dataclass
class RawVocab:
    phrases: list[str]
    target: str
    line: int


@dataclass
class RawGuardrail:
    guardrail_id: str | None
    text: str
    line: int


@dataclass
class RawGolden:
    golden_id: str
    questions: list[str]
    looker_query: dict[str, Any] | None
    explore_url: str | None
    line: int


@dataclass
class ParsedSpec:
    path: Path
    rel: str  # path relative to the config root (for provenance)
    front: Frontmatter
    front_line: int
    role: tuple[str, int] | None = None
    audience: tuple[str, int] | None = None
    rules: list[RawRule] = field(default_factory=list)
    vocabulary: list[RawVocab] = field(default_factory=list)
    guardrails: list[RawGuardrail] = field(default_factory=list)
    golden: list[RawGolden] = field(default_factory=list)
    section_lines: dict[str, int] = field(default_factory=dict)


def _yaml_items(text: str, first_line: int, rel: str, section: str) -> list[tuple[Any, int]]:
    """Parse a section body as a YAML list; return (value, absolute line) per item."""
    if not text.strip():
        return []
    try:
        node = yaml.compose(text)
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = first_line + (mark.line if mark is not None else 0)
        raise SpecError(
            f"section '## {section}' is not valid YAML: {exc}", file=rel, line=line
        ) from exc
    if not isinstance(node, yaml.SequenceNode) or not isinstance(data, list):
        raise SpecError(
            f"section '## {section}' must be a YAML list (lines starting with '- ')",
            file=rel,
            line=first_line,
        )
    return [(v, first_line + n.start_mark.line) for v, n in zip(data, node.value, strict=True)]


def _require(
    d: dict[str, Any], keys: set[str], allowed: set[str], rel: str, line: int, what: str
) -> None:
    missing = keys - set(d)
    extra = set(d) - allowed
    if missing:
        raise SpecError(f"{what} is missing {sorted(missing)}", file=rel, line=line)
    if extra:
        raise SpecError(
            f"{what} has unknown keys {sorted(extra)}; allowed: {sorted(allowed)}",
            file=rel,
            line=line,
        )


def parse_text(text: str, *, path: Path, rel: str) -> ParsedSpec:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SpecError(
            "agent spec must start with a '---' YAML frontmatter block", file=rel, line=1
        )
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration as exc:
        raise SpecError("frontmatter is not closed with '---'", file=rel, line=1) from exc
    try:
        fm_data = yaml.safe_load("\n".join(lines[1:end])) or {}
        front = Frontmatter.model_validate(fm_data)
    except yaml.YAMLError as exc:
        raise SpecError(f"frontmatter is not valid YAML: {exc}", file=rel, line=2) from exc
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors()
        )
        raise SpecError(
            f"invalid frontmatter ({problems}). Allowed keys: agent, description, extends, explores, derive",
            file=rel,
            line=2,
        ) from exc
    if not AGENT_ID_RE.match(front.agent):
        raise SpecError(
            f"agent id {front.agent!r} must be lowercase letters, digits, '-' or '_'",
            file=rel,
            line=2,
        )
    for e in front.explores:
        if "::" not in e:
            raise SpecError(
                f"explore {e!r} must be written as <project>::<explore>", file=rel, line=2
            )

    spec = ParsedSpec(path=path, rel=rel, front=front, front_line=1)
    # split body into sections
    current: str | None = None
    buf: list[str] = []
    start = 0
    bodies: list[tuple[str, int, str]] = []

    def flush() -> None:
        if current is not None:
            bodies.append((current, start, "\n".join(buf)))

    for i in range(end + 1, len(lines)):
        line = lines[i]
        m = re.match(r"^(#{1,6})\s+(.*?)\s*$", line)
        if m and len(m.group(1)) == 2:
            flush()
            name = m.group(2)
            key = _SECTION_KEYS.get(name.lower())
            if key is None:
                raise SpecError(
                    f"unknown section '## {name}'. Known sections: {', '.join(SECTIONS)}",
                    file=rel,
                    line=i + 1,
                )
            if key in spec.section_lines:
                raise SpecError(f"section '## {key}' appears twice", file=rel, line=i + 1)
            spec.section_lines[key] = i + 1
            current, start, buf = key, i + 2, []
        elif m and len(m.group(1)) == 1 and current is None:
            continue  # optional document title
        elif m and len(m.group(1)) > 2:
            raise SpecError(
                "only '## Section' headings are allowed in agent specs", file=rel, line=i + 1
            )
        elif current is None:
            if line.strip():
                raise SpecError("text outside a '## Section'", file=rel, line=i + 1)
        else:
            buf.append(line)
    flush()

    for name, first, body in bodies:
        if name in ("Role", "Audience"):
            content = body.strip()
            offset = next((n for n, ln in enumerate(body.splitlines()) if ln.strip()), 0)
            val = (content, first + offset) if content else None
            if name == "Role":
                spec.role = val
            else:
                spec.audience = val
        elif name == "Rules":
            for item, ln in _yaml_items(body, first, rel, name):
                if not isinstance(item, dict):
                    raise SpecError(
                        "each rule must be a mapping with 'id' and 'text'", file=rel, line=ln
                    )
                _require(item, {"id", "text"}, {"id", "text", "locked"}, rel, ln, "rule")
                spec.rules.append(
                    RawRule(
                        str(item["id"]),
                        str(item["text"]).strip(),
                        bool(item.get("locked", False)),
                        ln,
                    )
                )
        elif name == "Vocabulary":
            for off, raw in enumerate(body.splitlines()):
                if not raw.strip():
                    continue
                vm = VOCAB_RE.match(raw.strip())
                if not vm:
                    raise SpecError(
                        'vocabulary lines look like: - "phrase", "other phrase" → explore.field',
                        file=rel,
                        line=first + off,
                    )
                phrases = re.findall(r'"([^"]+)"', vm.group(1))
                spec.vocabulary.append(RawVocab(phrases, vm.group(2), first + off))
        elif name == "Guardrails":
            for item, ln in _yaml_items(body, first, rel, name):
                if isinstance(item, str):
                    spec.guardrails.append(RawGuardrail(None, item.strip(), ln))
                elif isinstance(item, dict):
                    _require(item, {"id", "text"}, {"id", "text"}, rel, ln, "guardrail")
                    spec.guardrails.append(
                        RawGuardrail(str(item["id"]), str(item["text"]).strip(), ln)
                    )
                else:
                    raise SpecError(
                        "guardrails are strings or {id, text} mappings", file=rel, line=ln
                    )
        elif name == "Golden queries":
            for item, ln in _yaml_items(body, first, rel, name):
                if not isinstance(item, dict):
                    raise SpecError("each golden query must be a mapping", file=rel, line=ln)
                _require(
                    item,
                    {"id", "questions"},
                    {"id", "questions", "looker_query", "explore_url"},
                    rel,
                    ln,
                    "golden query",
                )
                if ("looker_query" in item) == ("explore_url" in item):
                    raise SpecError(
                        "golden query needs exactly one of looker_query or explore_url",
                        file=rel,
                        line=ln,
                    )
                qs = item["questions"]
                if not isinstance(qs, list) or not qs or not all(isinstance(q, str) for q in qs):
                    raise SpecError(
                        "golden query 'questions' must be a non-empty list of strings",
                        file=rel,
                        line=ln,
                    )
                lq = item.get("looker_query")
                if lq is not None and not isinstance(lq, dict):
                    raise SpecError("looker_query must be a mapping", file=rel, line=ln)
                spec.golden.append(
                    RawGolden(
                        str(item["id"]), [q.strip() for q in qs], lq, item.get("explore_url"), ln
                    )
                )
    return spec


def parse_file(path: Path, root: Path) -> ParsedSpec:
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = path.as_posix()
    if not path.exists():
        raise SpecError("agent spec file not found", file=rel, line=1, rule_id="LKS011")
    return parse_text(path.read_text(encoding="utf-8"), path=path, rel=rel)
