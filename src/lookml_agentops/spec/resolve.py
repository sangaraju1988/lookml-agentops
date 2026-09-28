"""Resolve ``extends`` chains and enforce ``locked`` rules, producing an (unbound) AgentSpec.

* ``extends`` is generic: any spec may extend any others; ancestors resolve depth-first in the
  listed order, each spec at most once; cycles are an error.
* Rule ids must be unique across the whole chain (duplicates are an error, LKS007).
* A descendant overrides an ancestor rule by making a conflicting *claim* (e.g. a different
  meaning for "revenue") under its own rule id. Overriding a ``locked: true`` rule is a compile
  error (LKS008). Vocabulary entries conflicting with a locked rule are checked once the spec is
  bound to a model (see :mod:`lookml_agentops.generate.bind`).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from lookml_agentops.spec.errors import SpecError
from lookml_agentops.spec.interpret import guardrail_pii_kinds, rule_claims
from lookml_agentops.spec.model import (
    AgentSpec,
    ChainEntry,
    ExploreBinding,
    GoldenQuery,
    Guardrail,
    LookerQuery,
    Rule,
    SourceRef,
    TextBlock,
    VocabEntry,
)
from lookml_agentops.spec.parse import ParsedSpec, parse_file


def _load_chain(
    path: Path, root: Path, stack: list[str], seen: dict[Path, ParsedSpec], order: list[ParsedSpec]
) -> ParsedSpec:
    key = path.resolve()
    rel = (
        key.relative_to(root.resolve()).as_posix()
        if key.is_relative_to(root.resolve())
        else str(path)
    )
    if rel in stack:
        raise SpecError(
            "extends cycle: " + " -> ".join([*stack[stack.index(rel) :], rel]),
            file=stack[-1],
            line=1,
            rule_id="LKS011",
        )
    if key in seen:
        return seen[key]
    parsed = parse_file(key, root)
    for parent in parsed.front.extends:
        ppath = key.parent / parent
        if not ppath.exists():
            raise SpecError(
                f"extends target {parent!r} not found", file=parsed.rel, line=2, rule_id="LKS011"
            )
        _load_chain(ppath, root, [*stack, rel], seen, order)
    seen[key] = parsed
    order.append(parsed)
    return parsed


def guardrail_id(agent: str, text: str) -> str:
    return f"{agent}-g-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:8]}"


def resolve_spec(path: Path, root: Path) -> AgentSpec:
    """Load ``path`` and its extends chain; merge in chain order (ancestors first)."""
    order: list[ParsedSpec] = []
    leaf = _load_chain(path, root, [], {}, order)
    ids = [p.front.agent for p in order]
    dup_agents = sorted({i for i in ids if ids.count(i) > 1})
    if dup_agents:
        raise SpecError(
            f"agent id(s) {dup_agents} defined by more than one spec in the chain", file=leaf.rel
        )

    rules: list[Rule] = []
    by_id: dict[str, Rule] = {}
    claim_owner: dict[tuple[str, str], Rule] = {}
    vocab: list[VocabEntry] = []
    guardrails: list[Guardrail] = []
    golden: list[GoldenQuery] = []
    golden_ids: dict[str, SourceRef] = {}
    role = audience = None
    explores: list[str] = []
    for p in order:
        agent = p.front.agent
        for r in p.rules:
            src = SourceRef(file=p.rel, line=r.line)
            if r.rule_id in by_id:
                other = by_id[r.rule_id]
                raise SpecError(
                    f"duplicate rule id {r.rule_id!r} (also defined at {other.source}); rule ids must be "
                    "unique across the extends chain",
                    file=p.rel,
                    line=r.line,
                    rule_id="LKS007",
                )
            rule = Rule(
                rule_id=r.rule_id,
                text=r.text,
                locked=r.locked,
                origin_agent=agent,
                source=src,
                claims=rule_claims(r.text),
            )
            for c in rule.claims:
                prev = claim_owner.get((c.kind, c.subject))
                if (
                    prev is not None
                    and prev.origin_agent != agent
                    and any(
                        pc.subject == c.subject and pc.kind == c.kind and pc.value != c.value
                        for pc in prev.claims
                    )
                ):
                    if prev.locked:
                        raise SpecError(
                            f"rule {r.rule_id!r} contradicts locked rule {prev.rule_id!r} ({prev.source}) "
                            f"on {c.subject!r}: descendants may not override locked rules",
                            file=p.rel,
                            line=r.line,
                            rule_id="LKS008",
                        )
                    prev.overridden_by = r.rule_id
                claim_owner[(c.kind, c.subject)] = rule
            by_id[r.rule_id] = rule
            rules.append(rule)
        for v in p.vocabulary:
            vocab.append(
                VocabEntry(
                    vocab_id=f"{agent}-{re.sub(r'[^a-z0-9]+', '-', v.phrases[0].lower()).strip('-')}",
                    phrases=[x.lower() for x in v.phrases],
                    target=v.target,
                    origin_agent=agent,
                    source=SourceRef(file=p.rel, line=v.line),
                )
            )
        for g in p.guardrails:
            guardrails.append(
                Guardrail(
                    guardrail_id=g.guardrail_id or guardrail_id(agent, g.text),
                    text=g.text,
                    origin_agent=agent,
                    source=SourceRef(file=p.rel, line=g.line),
                    pii_kinds=guardrail_pii_kinds(g.text),
                )
            )
        for q in p.golden:
            src = SourceRef(file=p.rel, line=q.line)
            if q.golden_id in golden_ids:
                raise SpecError(
                    f"duplicate golden query id {q.golden_id!r} (also at {golden_ids[q.golden_id]})",
                    file=p.rel,
                    line=q.line,
                    rule_id="LKS007",
                )
            golden_ids[q.golden_id] = src
            lq = None
            if q.looker_query is not None:
                try:
                    lq = LookerQuery.model_validate(q.looker_query)
                except Exception as exc:
                    raise SpecError(
                        f"invalid looker_query in {q.golden_id!r}: {exc}", file=p.rel, line=q.line
                    ) from exc
            golden.append(
                GoldenQuery(
                    golden_id=q.golden_id,
                    questions=q.questions,
                    looker_query=lq,
                    explore_url=q.explore_url,
                    resolved_from="inline" if lq else "unresolved",
                    origin_agent=agent,
                    source=src,
                )
            )
        if p.role:
            role = TextBlock(text=p.role[0], source=SourceRef(file=p.rel, line=p.role[1]))
        if p.audience:
            audience = TextBlock(
                text=p.audience[0], source=SourceRef(file=p.rel, line=p.audience[1])
            )
        for e in p.front.explores:
            if e not in explores:
                explores.append(e)

    bindings = [
        ExploreBinding(project=e.split("::", 1)[0], explore=e.split("::", 1)[1]) for e in explores
    ]
    return AgentSpec(
        agent_id=leaf.front.agent,
        description=leaf.front.description,
        source=SourceRef(file=leaf.rel, line=1),
        extends_chain=[ChainEntry(agent_id=p.front.agent, file=p.rel) for p in order[:-1]],
        explores=bindings,
        derive={k: v for k, v in leaf.front.derive.model_dump().items() if v},
        role=role,
        audience=audience,
        rules=rules,
        vocabulary=vocab,
        guardrails=guardrails,
        golden_queries=golden,
    )
