# Governance tag conventions

lookml-agentops reads LookML `tags:` on fields, views, and explores. The tags are ordinary LookML,
so Looker ignores them and they're safe to add.

| Tag | Applies to | Meaning |
|---|---|---|
| `certified` | measure | Owned and certified by the hub team. Spokes may refine its wording but **must not** change its `sql`/`type` (lint `LKA007`, compile error on contradiction). |
| `ai_exposed` | field | Curated for AI agents. Must link to an **approved** glossary term (`LKA003`). |
| `ai_hidden` | field | Never offered to AI agents, even if visible to humans. |
| `ai_default_time` | dimension_group | The default time field of its view (used for "last quarter" etc.). |
| `glossary:<term_id>` | field | Links the field to a business glossary term. |
| `pii` | field | Personal data. Must be `hidden: yes` or protected by `required_access_grants` (`LKA005`). Compiles into a `pii_guardrail` rule. |
| `pii:<kind>` | field | Kind of PII: `email`, `phone`, `dob`, `address`, `name`, `ssn`. |
| `fiscal_calendar` | view | The fiscal calendar view (drives the `time_convention` rule). |
| `fiscal_reporting` | explore | Explore reports by fiscal period. Every exposed time field needs a fiscal-calendar join (`LKA010`). |

## Lint exemptions

Put this on the line above an object, or at the end of its opening line:

```lookml
# lkagent:disable LKA010 reason="delivery timestamps are operational, not fiscal"
dimension_group: delivered { ... }
```

An exemption without a `reason` is reported as a warning (`LKA000`).
