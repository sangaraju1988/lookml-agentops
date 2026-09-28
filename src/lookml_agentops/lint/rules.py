"""Lint rules. Each rule has a stable ID, default severity, rationale and fix hint.

Objects from an imported project appear in every importing project's effective model; the engine
de-duplicates identical findings, so rules can simply iterate over every model.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from lookml_agentops.lint.engine import LintContext, RawFinding, Rule
from lookml_agentops.lookml.model import LExplore, LField, Loc, LView, expand_field_names

PII_NAME_RX = re.compile(
    r"(e_?mail|phone|mobile|\bdob\b|date_of_birth|birth_?date|ssn|social_security|"
    r"tax_id|passport|address|street|postal|zip_?code)",
    re.IGNORECASE,
)
TIME_KINDS = {"time", "date"}


def _fields(ctx: LintContext) -> Iterator[tuple[str, LView, LField]]:
    for project, em in ctx.models.items():
        for v in em.views.values():
            for f in v.fields.values():
                yield project, v, f


def _raw(f: LField, message: str) -> RawFinding:
    return RawFinding(message, f.id, f.last_at, frozenset(f.exemptions))


def _explores(ctx: LintContext) -> Iterator[tuple[str, LExplore]]:
    for project, em in ctx.models.items():
        for e in em.explores.values():
            yield project, e


def _explore_raw(e: LExplore, message: str) -> RawFinding:
    return RawFinding(message, f"explore {e.name}", e.provenance[-1].loc, frozenset(e.exemptions))


def _exposed(f: LField) -> bool:
    return not f.hidden and "ai_hidden" not in f.tags


# ---- LKA000 ------------------------------------------------------------------------------------
def check_resolution(ctx: LintContext) -> Iterator[RawFinding]:
    for project, em in ctx.models.items():
        for p in em.problems:
            yield RawFinding(f"LookML resolution problem: {p}", f"project {project}", None)
    for project in ctx.models:
        for rel, pf in ctx.ws.projects[project].files.items():
            for line, msg in pf.exemption_problems:
                yield RawFinding(msg, f"{project}/{rel}", Loc(project, rel, line))


# ---- field metadata ----------------------------------------------------------------------------
def check_missing_description(ctx: LintContext) -> Iterator[RawFinding]:
    for _, _, f in _fields(ctx):
        if not f.hidden and not (f.description or "").strip():
            yield _raw(f, f"{f.id}: visible {f.kind} has no description")


def check_ai_exposed_label(ctx: LintContext) -> Iterator[RawFinding]:
    for _, _, f in _fields(ctx):
        if f.has_tag("ai_exposed") and not (f.label or "").strip():
            yield _raw(f, f"{f.id}: ai_exposed field has no label")


def check_ai_exposed_glossary(ctx: LintContext) -> Iterator[RawFinding]:
    terms = ctx.catalog.by_id()
    for _, _, f in _fields(ctx):
        if not f.has_tag("ai_exposed"):
            continue
        tagged = [terms[g] for g in f.glossary_ids() if g in terms]
        linking = ctx.catalog.terms_linking(f.origin_project, f.view, f.name)
        if not any(t.approved for t in [*tagged, *linking]):
            yield _raw(f, f"{f.id}: ai_exposed field is not linked to an approved glossary term")


def check_pii_untagged(ctx: LintContext) -> Iterator[RawFinding]:
    for _, _, f in _fields(ctx):
        if f.has_tag("pii") or f.kind in ("measure", "filter", "parameter"):
            continue
        cols = re.findall(r"\$\{TABLE\}\.(\w+)", f.sql or "")
        hits = [c for c in [f.name, *cols] if PII_NAME_RX.search(c)]
        if hits:
            yield _raw(f, f"{f.id}: looks like PII ({hits[0]!r}) but has no 'pii' tag")


def check_pii_unprotected(ctx: LintContext) -> Iterator[RawFinding]:
    for _, _, f in _fields(ctx):
        if f.has_tag("pii") and not f.hidden and not f.required_access_grants:
            yield _raw(
                f, f"{f.id}: pii field is neither hidden nor protected by required_access_grants"
            )


def check_missing_value_format(ctx: LintContext) -> Iterator[RawFinding]:
    for _, _, f in _fields(ctx):
        if (
            f.kind == "measure"
            and not f.hidden
            and not f.value_format_name
            and "value_format" not in f.params
        ):
            yield _raw(f, f"{f.id}: measure has no value_format_name")


def check_conflicting_ai_tags(ctx: LintContext) -> Iterator[RawFinding]:
    for _, _, f in _fields(ctx):
        if f.has_tag("ai_exposed") and (f.hidden or f.has_tag("ai_hidden")):
            yield _raw(f, f"{f.id}: tagged ai_exposed but also hidden/ai_hidden")


def check_certified_glossary(ctx: LintContext) -> Iterator[RawFinding]:
    for _, _, f in _fields(ctx):
        if f.kind == "measure" and f.has_tag("certified") and not f.glossary_ids():
            yield _raw(f, f"{f.id}: certified measure has no glossary:<term> tag")


def check_unknown_glossary_tag(ctx: LintContext) -> Iterator[RawFinding]:
    terms = ctx.catalog.by_id()
    for _, _, f in _fields(ctx):
        for g in f.glossary_ids():
            if g not in terms:
                yield _raw(f, f"{f.id}: tag glossary:{g} references an unknown glossary term")


def check_access_grant_defined(ctx: LintContext) -> Iterator[RawFinding]:
    for _project, em in ctx.models.items():
        for v in em.views.values():
            for f in v.fields.values():
                for g in f.required_access_grants:
                    if g not in em.access_grants:
                        yield _raw(
                            f,
                            f"{f.id}: required_access_grants references {g!r}, which is not "
                            f"defined in the model of project {em.project}",
                        )


# ---- cross-project -----------------------------------------------------------------------------
def _norm_sql(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def check_duplicate_measure_sql(ctx: LintContext) -> Iterator[RawFinding]:
    seen: dict[str, dict[tuple[str, str], list[tuple[str, LField]]]] = {}
    for project, em in ctx.models.items():
        for e in em.queryable_explores() or list(em.explores.values()):
            for alias, v in em.explore_views(e).items():
                for f in v.fields.values():
                    if f.kind != "measure":
                        continue
                    key = (f.type, _norm_sql(f.sql) + "|" + str(f.params.get("filters", "")))
                    seen.setdefault(f.name, {}).setdefault(key, []).append(
                        (f"{project}:{e.name}.{alias}", f)
                    )
    for name, variants in sorted(seen.items()):
        if len(variants) < 2:
            continue
        places = sorted({where for vs in variants.values() for where, _ in vs})
        for vs in variants.values():
            for _, f in vs:
                yield _raw(
                    f,
                    f"measure name {name!r} has {len(variants)} different definitions across "
                    f"explores/projects ({', '.join(places)})",
                )


def check_certified_redefined(ctx: LintContext) -> Iterator[RawFinding]:
    """A project must not change the SQL/type/filters of a certified measure it imports."""
    for project, em in ctx.models.items():
        for v in em.views.values():
            for f in v.fields.values():
                origin = f.origin_project
                if f.kind != "measure" or origin == project or origin not in ctx.models:
                    continue
                of = ctx.models[origin].field(v.name, f.name)
                if of is None or not of.has_tag("certified"):
                    continue
                if (
                    _norm_sql(f.sql) != _norm_sql(of.sql)
                    or f.type != of.type
                    or f.params.get("filters") != of.params.get("filters")
                ):
                    yield _raw(
                        f,
                        f"{f.id}: project {project} redefines certified measure owned by {origin} "
                        f"(sql/type/filters changed at {f.last_at}); importing projects may add, not redefine",
                    )


def check_test_account_guard(ctx: LintContext) -> Iterator[RawFinding]:
    for _project, e in _explores(ctx):
        em = next(
            m for m in ctx.models.values() if e.name in m.explores and m.explores[e.name] is e
        )
        guarded_views = [
            a
            for a, v in em.explore_views(e).items()
            if any(
                f.name == "is_test_account" or "is_test_account" in (f.sql or "")
                for f in v.fields.values()
            )
        ]
        if guarded_views and "is_test_account" not in (e.sql_always_where or ""):
            yield _explore_raw(
                e,
                f"explore {e.name} includes {', '.join(guarded_views)} (has is_test_account) "
                "but sql_always_where does not exclude test accounts",
            )


def check_fiscal_join(ctx: LintContext) -> Iterator[RawFinding]:
    for _project, e in _explores(ctx):
        if "fiscal_reporting" not in e.tags:
            continue
        em = next(
            m for m in ctx.models.values() if e.name in m.explores and m.explores[e.name] is e
        )
        fiscal_ons = [
            j.sql_on or ""
            for j in e.joins.values()
            if j.from_view in em.views and "fiscal_calendar" in em.views[j.from_view].tags
        ]
        for alias, v in em.explore_views(e).items():
            if "fiscal_calendar" in v.tags:
                continue
            for f in v.fields.values():
                if f.kind != "dimension_group" or f.hidden or f.type not in TIME_KINDS:
                    continue
                if not any(f"${{{alias}.{f.name}_" in on for on in fiscal_ons):
                    yield _raw(
                        f,
                        f"{alias}.{f.name}: date field in fiscal_reporting explore {e.name} has no "
                        "fiscal calendar join",
                    )


def check_dangling_glossary_link(ctx: LintContext) -> Iterator[RawFinding]:
    known: set[str] = set()
    for _, _, f in _fields(ctx):
        known.add(f"{f.origin_project}.{f.view}.{f.name}")
    for t in ctx.catalog.terms:
        for link in t.linked_fields:
            if link not in known:
                yield RawFinding(
                    f"glossary term {t.id!r} links to {link}, which does not exist in any effective model",
                    f"term {t.id}",
                    ctx.catalog_loc(t.id),
                )


def check_ai_surface_area(ctx: LintContext) -> Iterator[RawFinding]:
    limit = ctx.cfg.lint.max_ai_fields
    for _project, em in ctx.models.items():
        for e in em.queryable_explores():
            n = sum(
                len(expand_field_names(f))
                for v in em.explore_views(e).values()
                for f in v.fields.values()
                if _exposed(f) and not f.has_tag("pii")
            )
            if n > limit:
                yield _explore_raw(e, f"explore {e.name} exposes {n} fields to AI (limit {limit})")


def check_synonym_collision(ctx: LintContext) -> Iterator[RawFinding]:
    owners: dict[str, list[str]] = {}
    for t in ctx.catalog.terms:
        if not t.approved:
            continue
        for phrase in {t.name.lower(), *(s.lower() for s in t.synonyms)}:
            owners.setdefault(phrase, []).append(t.id)
    for phrase, ids in sorted(owners.items()):
        if len(ids) > 1:
            for tid in sorted(ids):
                yield RawFinding(
                    f"phrase {phrase!r} is claimed by several approved terms: {', '.join(sorted(ids))}",
                    f"term {tid}",
                    ctx.catalog_loc(tid),
                )


def check_explore_description(ctx: LintContext) -> Iterator[RawFinding]:
    for _project, em in ctx.models.items():
        for e in em.queryable_explores():
            if not (e.description or "").strip():
                yield _explore_raw(
                    e, f"explore {e.name} has no description (agents use it to choose explores)"
                )


ALL_RULES: list[Rule] = [
    Rule(
        "LKA000",
        "lookml-problem",
        "warning",
        "Unresolvable includes/refinements or malformed exemptions make every other check unreliable.",
        'Fix the include path, manifest dependency or exemption comment (add reason="...").',
        check_resolution,
    ),
    Rule(
        "LKA001",
        "missing-description",
        "warning",
        "Agents pick fields from descriptions; an undescribed visible field is a guess.",
        "Add a `description:` explaining what the field means and when to use it.",
        check_missing_description,
    ),
    Rule(
        "LKA002",
        "ai-field-missing-label",
        "warning",
        "Labels are what agents and users see; AI-exposed fields need a stable business label.",
        "Add a `label:`.",
        check_ai_exposed_label,
    ),
    Rule(
        "LKA003",
        "ai-field-not-in-glossary",
        "error",
        "Fields curated for AI must map to an approved business definition so instructions compile.",
        "Tag with glossary:<term_id> and approve the term, or remove ai_exposed.",
        check_ai_exposed_glossary,
    ),
    Rule(
        "LKA004",
        "pii-untagged",
        "error",
        "A column that looks like personal data without a pii tag can leak through an agent.",
        'Add tags: ["pii", "pii:<kind>"] and hide or grant-protect the field.',
        check_pii_untagged,
    ),
    Rule(
        "LKA005",
        "pii-unprotected",
        "error",
        "PII must never be reachable by default by agents or users.",
        "Set `hidden: yes` or `required_access_grants: [...]`.",
        check_pii_unprotected,
    ),
    Rule(
        "LKA006",
        "ambiguous-measure-name",
        "warning",
        "The same measure name meaning different things in different places confuses agents.",
        "Rename one of the measures or align their SQL.",
        check_duplicate_measure_sql,
    ),
    Rule(
        "LKA007",
        "certified-measure-redefined",
        "error",
        "A project that imports a certified metric may add metrics but must not redefine it.",
        "Change the metric in the project that owns it (with review), or add a differently named measure.",
        check_certified_redefined,
    ),
    Rule(
        "LKA008",
        "missing-test-account-guard",
        "error",
        "Test accounts inflate every metric; explores over them need a permanent guard.",
        "Add `sql_always_where: NOT ${customers.is_test_account} ;;`.",
        check_test_account_guard,
    ),
    Rule(
        "LKA009",
        "missing-value-format",
        "note",
        "Unformatted measures produce ambiguous answers (dollars? percent?).",
        "Add `value_format_name:` (usd, percent_1, decimal_0, ...).",
        check_missing_value_format,
    ),
    Rule(
        "LKA010",
        "date-without-fiscal-join",
        "warning",
        "In fiscal-reporting explores, 'last quarter' must resolve through the fiscal calendar.",
        "Join fiscal_calendar on this date (from: fiscal_calendar) or hide the field.",
        check_fiscal_join,
    ),
    Rule(
        "LKA011",
        "dangling-glossary-link",
        "error",
        "A glossary link to a missing field silently drops a rule from compiled instructions.",
        "Fix linked_fields (<project>.<view>.<field>) or restore the field.",
        check_dangling_glossary_link,
    ),
    Rule(
        "LKA012",
        "ai-surface-area",
        "warning",
        "Large explores reduce agent accuracy; curate what the agent can see.",
        "Hide or tag ai_hidden fields the agent does not need, or split the explore.",
        check_ai_surface_area,
    ),
    Rule(
        "LKA013",
        "conflicting-ai-tags",
        "warning",
        "A field cannot be both curated for AI and hidden from it.",
        "Remove either ai_exposed or hidden/ai_hidden.",
        check_conflicting_ai_tags,
    ),
    Rule(
        "LKA014",
        "certified-without-glossary",
        "warning",
        "Certified metrics are the backbone of metric_definition rules and need a glossary term.",
        "Add a glossary:<term_id> tag.",
        check_certified_glossary,
    ),
    Rule(
        "LKA015",
        "glossary-synonym-collision",
        "error",
        "Two approved terms claiming the same phrase make agent vocabulary ambiguous.",
        "Remove the synonym from one of the terms.",
        check_synonym_collision,
    ),
    Rule(
        "LKA016",
        "explore-missing-description",
        "warning",
        "Agents choose explores by description.",
        "Add an explore `description:`.",
        check_explore_description,
    ),
    Rule(
        "LKA017",
        "unknown-glossary-tag",
        "error",
        "A glossary tag pointing nowhere means the field's business meaning is undefined.",
        "Create the term or fix the tag.",
        check_unknown_glossary_tag,
    ),
    Rule(
        "LKA018",
        "undefined-access-grant",
        "error",
        "Fields protected by an undefined grant are not protected the way the author intended.",
        "Define the access_grant in every model that includes the view.",
        check_access_grant_defined,
    ),
]
