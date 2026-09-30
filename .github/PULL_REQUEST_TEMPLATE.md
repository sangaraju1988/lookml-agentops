## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## Checklist

- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run mypy`
- [ ] `uv run pytest`
- [ ] `uv run lkagent generate compile -c examples/harborline --check` (if specs, LookML or exporters changed)
- [ ] Docs updated (README / `docs/`) for user-visible changes
- [ ] Vendor API fields are confirmed in official docs, or marked `TODO(verify-api)` and listed in `docs/api-verification.md`
- [ ] No real company, person or customer names; synthetic data only; no credentials or row-level data
