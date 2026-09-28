# Contributing

Thanks for helping! This is a small, serious project. Please keep changes focused.

## Setup

```bash
uv sync --all-extras
uv run pre-commit install
```

## Before you open a PR

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
uv run lkagent generate compile -c examples/harborline --check   # build/ is current
```

## Ground rules

* **Determinism.** Same inputs must produce byte-identical artifacts. Sort keys, fix seeds, and
  keep timestamps out of anything that gets diffed. Run metadata belongs in `.lkagent/`.
* **Synthetic data only.** The demo company is the fictional *Harborline Supply Co.* Don't add
  real company, person or customer names anywhere.
* **Don't guess vendor APIs.** Anything touching Looker Conversational Analytics, the Looker MCP
  server or Dataplex must cite the official docs. Unconfirmed fields go behind an interface
  with `# TODO(verify-api)` and an entry in `docs/api-verification.md`.
* **Dependencies.** Discuss new runtime dependencies in an issue first.
* Commits follow Conventional Commits (`feat(lint): …`, `fix(verify): …`).
