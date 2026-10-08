# AGENTS.md

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues for `troyan-dy/nuke-di` via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default triage labels are used as-is: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### README translations

`README.md` has translations in `docs/i18n/`: `README.ru.md`, `README.zh-CN.md`, `README.es.md`, `README.pt-BR.md`, `README.ja.md`, `README.pl.md`. A change to `README.md` is mirrored in every translation in the same pull request. Code blocks are copied verbatim, never translated; translated headings keep the English anchor as `## <a id="english-anchor"></a>Heading`. Links to repository files go up two levels (`../../LICENSE`). `tests/test_readme_translations.py` fails when the code blocks or links drift from `README.md`.

## Design principles

Keep the apparent simplicity: a dependency is a class with a type-hinted `__init__` and `connect()` / `disconnect()`, and that is the whole model. A proposed feature whose result is already reachable with a `Client` subclass, `mock()` / `override()` or a README recipe becomes a recipe, however common the feature is in other DI libraries. The reasoning and the rejected designs: `docs/adr/0005-third-party-objects-as-client-classes.md`; earlier rejections are issues #9 (binding a Protocol to an implementation) and #11 (connect retries).
