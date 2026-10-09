# AGENTS.md

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues for `troyan-dy/nuke-di` via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default triage labels are used as-is: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### README and guide translations

`README.md` is the landing page: the pitch, the Quick start, the principles, the performance figures and a few runnable examples. The full documentation is the guide, one page per topic in `docs/guide/`, linked from the README "Documentation" section; a new feature is documented there, and reaches the README only when it belongs in the pitch.

Both have translations: `docs/i18n/README.<language>.md` and `docs/i18n/<language>/<page>.md` for `ru`, `zh-CN`, `es`, `pt-BR`, `ja`, `pl`. A change to `README.md` or a guide page is mirrored in every translation in the same pull request. Code blocks are copied verbatim, never translated; translated headings keep the English anchor as `## <a id="english-anchor"></a>Heading`. Links to repository files are relative to the translated file (`../../LICENSE` from a README translation, `../../../LICENSE` from a guide page). `tests/test_readme_translations.py` fails when the code blocks, sections or links drift from the English page.

## Design principles

Keep the apparent simplicity: a dependency is a class with a type-hinted `__init__` and `connect()` / `disconnect()`, and that is the whole model. A proposed feature whose result is already reachable with a `Client` subclass, `mock()` / `override()` or a README recipe becomes a recipe, however common the feature is in other DI libraries. The reasoning and the rejected designs: `docs/adr/0005-third-party-objects-as-client-classes.md`; earlier rejections are issues #9 (binding a Protocol to an implementation) and #11 (connect retries).
