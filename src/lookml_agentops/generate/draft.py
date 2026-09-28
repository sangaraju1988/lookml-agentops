"""``lkagent generate new``: draft a ``*.agent.md`` from a plain-English description.

The LLM is used **only here**; ``compile`` never calls one. A draft is grounded in the resolved
model (the chosen explores, their fields, labels and descriptions) and the catalog, then parsed,
written for human review and linted immediately.

Providers sit behind :class:`DraftProvider`:

* ``stub``    — deterministic, offline template (used in tests and when no LLM is configured);
* ``command`` — runs the shell-free command in ``LKAGENT_LLM_COMMAND`` (e.g. a local LLM CLI),
  sending the grounded prompt on stdin and reading Markdown from stdout. No new dependency and
  no credentials handled by lkagent.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from lookml_agentops.catalog import load_catalog
from lookml_agentops.config import LkagentConfig
from lookml_agentops.generate.bind import BoundExplore, exposed_fields
from lookml_agentops.generate.compile import load_models
from lookml_agentops.spec.errors import SpecError
from lookml_agentops.spec.parse import parse_text

PROMPT = """You write Looker Conversational Analytics agent specs in lookml-agentops' *.agent.md format.

Format (YAML frontmatter, then only these sections: Role, Audience, Rules, Vocabulary, Guardrails,
Golden queries). Rules are a YAML list of {{id, text, locked?}} with stable kebab-case ids. Write
definitions as '"<phrase>" means <metric label>.' Vocabulary lines look like
- "phrase" → explore.field. Golden queries use inline looker_query objects (no pivots).
Only use the explores and fields listed below. Keep field synonyms that already exist in LookML or
the catalog out of Vocabulary. Add a guardrail if any listed field is tagged pii.

Agent id: {agent}
Request: {description}
{extends}
Explores and fields:
{grounding}
Catalog terms:
{terms}

Return only the Markdown file."""


class DraftError(Exception):
    pass


@dataclass
class DraftRequest:
    agent: str
    description: str
    explores: list[str]  # project::explore
    extends: list[str] = field(default_factory=list)
    grounding: dict[str, Any] = field(default_factory=dict)

    def prompt(self) -> str:
        g = self.grounding
        lines = []
        for e in g.get("explores", []):
            lines.append(f"- {e['id']} ({e['label']}): {e['description']}")
            lines += [
                f"    {f['field']} [{f['kind']}{', pii' if f['pii'] else ''}] {f['label']}: {f['description']}"
                for f in e["fields"]
            ]
        terms = [
            f"- {t['name']}: {t['definition']} (synonyms: {', '.join(t['synonyms'])})"
            for t in g.get("terms", [])
        ]
        return PROMPT.format(
            agent=self.agent,
            description=self.description,
            extends=f"Extends: {', '.join(self.extends)}\n" if self.extends else "",
            grounding="\n".join(lines) or "(none)",
            terms="\n".join(terms) or "(none)",
        )


class DraftProvider(Protocol):
    name: str

    def draft(self, req: DraftRequest) -> str: ...


class StubProvider:
    """Deterministic drafting from the request and grounding (no LLM)."""

    name = "stub"

    def draft(self, req: DraftRequest) -> str:
        g = req.grounding
        front: dict[str, Any] = {
            "agent": req.agent,
            "description": req.description.split(".")[0].strip()[:200],
        }
        if req.extends:
            front["extends"] = req.extends
        front["explores"] = req.explores
        front["derive"] = {"lookml_descriptions": True, "catalog_glossary": True}
        rules = []
        for m in re.finditer(
            r"\b([a-z][a-z ]{1,30}?) means ([a-z][a-z ]{1,40}?)(?=[,.;]|$)", req.description.lower()
        ):
            phrase, target = m.group(1).strip(), m.group(2).strip()
            rid = re.sub(r"[^a-z0-9]+", "-", f"{phrase}-means-{target.split()[0]}").strip("-")
            rules.append({"id": rid, "text": f'"{phrase.capitalize()}" means {target}.'})
        if re.search(r"exclud\w* test accounts?", req.description.lower()):
            rules.append(
                {
                    "id": "exclude-test-accounts",
                    "text": "Never include test accounts or soft-deleted orders, even when asked for all data.",
                }
            )
        pii = sorted(
            {f["label"].lower() for e in g.get("explores", []) for f in e["fields"] if f["pii"]}
        )
        out = [
            "---",
            yaml.safe_dump(front, sort_keys=False).rstrip(),
            "---",
            "",
            "## Role",
            f"You are a data analyst for this request: {req.description.strip()}",
            "",
            "## Audience",
            "Business users who are not SQL experts.",
            "",
        ]
        if rules:
            out += ["## Rules", yaml.safe_dump(rules, sort_keys=False, width=1000).rstrip(), ""]
        if pii:
            out += ["## Guardrails", f"- Never return {', '.join(pii)}.", ""]
        return "\n".join(out)


class CommandProvider:
    """Pipe the grounded prompt to an external LLM command (``LKAGENT_LLM_COMMAND``)."""

    name = "command"

    def __init__(self, command: str | None = None, timeout: float = 300.0) -> None:
        cmd = command or os.environ.get("LKAGENT_LLM_COMMAND")
        if not cmd:
            raise DraftError(
                "set LKAGENT_LLM_COMMAND to a command that reads a prompt on stdin "
                "and prints Markdown, or use --provider stub"
            )
        self.argv = shlex.split(cmd)
        self.timeout = timeout

    def draft(self, req: DraftRequest) -> str:
        try:
            out = subprocess.run(
                self.argv,
                input=req.prompt(),
                capture_output=True,
                text=True,
                check=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DraftError(f"LLM command failed: {type(exc).__name__}") from exc
        text = out.stdout.strip()
        fence = re.search(r"```(?:markdown|md)?\n(.*?)```", text, re.S)
        return (fence.group(1) if fence else text).strip() + "\n"


def grounding(cfg: LkagentConfig, explores: list[str]) -> dict[str, Any]:
    models = load_models(cfg)
    catalog = load_catalog(cfg)
    out: dict[str, Any] = {"explores": [], "terms": []}
    seen_terms: set[str] = set()
    for ref in explores:
        project, _, name = ref.partition("::")
        em = models.get(project)
        e = em.explores.get(name) if em else None
        if em is None or e is None or e.extension_required:
            raise DraftError(
                f"explore {ref!r} not found (use <project>::<explore> from `lkagent graph`)"
            )
        be = BoundExplore(project, e, em)
        fields = []
        for fref, f in exposed_fields(be):
            if f.hidden and not f.has_tag("pii"):
                continue
            fields.append(
                {
                    "field": fref,
                    "kind": f.kind,
                    "label": f.display_label(),
                    "description": re.sub(r"\s+", " ", f.description or ""),
                    "pii": f.has_tag("pii"),
                }
            )
            for t in catalog.terms_linking(f.origin_project, f.view, f.name):
                if t.approved and t.id not in seen_terms:
                    seen_terms.add(t.id)
                    out["terms"].append(
                        {"name": t.name, "definition": t.definition, "synonyms": t.synonyms}
                    )
        out["explores"].append(
            {
                "id": ref,
                "label": e.label or name,
                "description": e.description or "",
                "fields": fields,
            }
        )
    return out


def draft_spec(
    cfg: LkagentConfig,
    req: DraftRequest,
    provider: DraftProvider,
    out_path: Path,
    *,
    force: bool = False,
) -> Path:
    if out_path.exists() and not force:
        raise DraftError(f"{out_path} exists; pass --force to overwrite")
    req.grounding = grounding(cfg, req.explores)
    text = provider.draft(req)
    try:
        parsed = parse_text(text, path=out_path, rel=out_path.name)
    except SpecError as exc:
        raise DraftError(f"the {provider.name} provider produced an invalid spec: {exc}") from exc
    if parsed.front.agent != req.agent:
        raise DraftError(f"draft declares agent {parsed.front.agent!r}, expected {req.agent!r}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return out_path
