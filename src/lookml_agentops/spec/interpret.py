"""Read machine-checkable *claims* out of free-text rules and guardrails.

Authors write plain sentences; these common phrasings become claims that drive lock checks,
generated tests, the mock agent and dependency tracing:

* ``"X" means Y`` / ``"X" and "Z" mean Y`` / ``"X" refers to Y``  -> vocabulary claim X -> Y
* a relative period ("last quarter", "last year", "last month") that ``means`` a completed
  fiscal quarter / fiscal year / calendar month                   -> time_period claim
* a guardrail mentioning email / phone / date of birth / address / contact name / SSN -> pii claim

A rule with no recognizable claim is still valid; it is compiled as prose and gets a presence test.
"""

from __future__ import annotations

import re

from lookml_agentops.spec.model import Claim

MEANS_RE = re.compile(
    r'^\s*((?:"[^"]+"\s*(?:,|and|or)?\s*)+)\s*(?:means?|refers? to|is|are)\s+(.+?)\s*(?:\bunless\b.*|[;.].*)?$',
    re.IGNORECASE,
)
RELATIVE = {
    "last quarter": ("fiscal quarter", "last_completed_fiscal_quarter"),
    "last fiscal quarter": ("fiscal quarter", "last_completed_fiscal_quarter"),
    "previous quarter": ("fiscal quarter", "last_completed_fiscal_quarter"),
    "last year": ("fiscal year", "last_completed_fiscal_year"),
    "last fiscal year": ("fiscal year", "last_completed_fiscal_year"),
    "last month": ("month", "last_completed_month"),
}
PII_KINDS = {
    "email": ["email", "e-mail"],
    "phone": ["phone"],
    "dob": ["date of birth", "birthday", "dob"],
    "address": ["address"],
    "name": ["contact name", "contact person"],
    "ssn": ["ssn", "social security"],
}


def normalize(s: str) -> str:
    s = re.sub(r"\s+", " ", s.strip().lower())
    return re.sub(r"^(the|a|an)\s+", "", s)


def rule_claims(text: str) -> list[Claim]:
    m = MEANS_RE.match(text)
    if not m:
        return []
    subjects = [normalize(p) for p in re.findall(r'"([^"]+)"', m.group(1))]
    target = normalize(m.group(2))
    out: list[Claim] = []
    for subj in subjects:
        if subj in RELATIVE:
            unit, period = RELATIVE[subj]
            if "completed" in target and unit in target:
                out.append(Claim(kind="time_period", subject=subj, value=period))
            else:
                out.append(Claim(kind="time_period", subject=subj, value=f"unrecognized:{target}"))
        else:
            out.append(Claim(kind="vocabulary", subject=subj, value=target))
    return out


def guardrail_pii_kinds(text: str) -> list[str]:
    low = text.lower()
    return sorted(
        k
        for k, words in PII_KINDS.items()
        if any(re.search(rf"\b{re.escape(w)}\b", low) for w in words)
    )
