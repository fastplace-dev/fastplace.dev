## Summary

What changed and why — one logical change per pull request.

## Rationale

The problem or need this addresses, and why this approach.

## Verification

- [ ] Tests written first (failing, then passing) — `pytest` and `npm run test:run` green
- [ ] Frontend touched — `npx playwright test` green
- [ ] Gates green:
  - [ ] `ruff format .` and `ruff check .`
  - [ ] `mypy fastplace packages/tenancy/src`
  - [ ] `fastplace lint:modules`
  - [ ] `npm run types`, `npm run lint:check`, `npm run format:check`
- [ ] Public API changed — docs updated (guides, docstrings, README)
- [ ] Bug fix — reproduction pinned by a test

## Notes for reviewers

Anything non-obvious, trade-offs taken, or follow-ups deliberately deferred.
