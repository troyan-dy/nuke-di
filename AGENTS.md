# AGENTS.md

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues for `troyan-dy/nuke-di` via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default triage labels are used as-is: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### README and guide

`README.md` is the landing page: the pitch, the Quick start, the principles, the performance figures and a few runnable examples. The full documentation is the guide, one page per topic in `docs/guide/`, linked from the README "Documentation" section; a new feature is documented there, and reaches the README only when it belongs in the pitch.

The documentation is written in English only: there are no translations, and none are added. Every link in the README and the guide lands on a file and a heading that exist (`tests/test_docs.py`).

`llms.txt` and `llms-full.txt` are generated from the README and the guide: after changing either, run `uv run python scripts/llms.py` (`tests/test_llms.py` fails otherwise). `skills/nuke-di/SKILL.md` is the page a coding agent reads before writing code with nuke-di: a change to the model, a recipe or a rejected design goes there too, and into the other places that list the rejected designs for agents: the `AGENTS.md` block of `docs/guide/agents.md`, the `rules` of `context7.json`, and `INTRO` of `scripts/llms.py`.

## Design principles

Keep the apparent simplicity: a dependency is a class with a type-hinted `__init__` and `connect()` / `disconnect()`, and that is the whole model. A proposed feature whose result is already reachable with a `Client` subclass, `mock()` / `override()` or a recipe in the guide becomes a recipe, however common the feature is in other DI libraries. The reasoning and the rejected designs: `docs/adr/0005-third-party-objects-as-client-classes.md` (#8, provider functions and connectors), `docs/adr/0008-clients-depend-on-concrete-clients.md` (#9, binding a Protocol to an implementation, qualifiers, multibinding) and `docs/adr/0009-connect-is-fail-fast.md` (#11, connect retries).

The application or framework owns process options, signals and task supervision. `@entrypoint` only runs an async function with connected clients when its module is executed. Use logs and dependency paths to diagnose lifecycle failures.

The library stays pure Python with no runtime dependencies: a Rust or compiled core was measured and is not added until a CPU-bound stage appears (`docs/adr/0010-pure-python-no-compiled-core.md`).

A client lives as long as its container. Per-request or per-message clients are never added (#65, `docs/adr/0006-clients-live-as-long-as-the-container.md`).

## Эксперимент с моделями Codex

- Для задач разработки основной агент отвечает за реализацию,
  проверку и исправление подтверждённых замечаний.
- Если точка входа неочевидна или нужно проследить поведение через
  несколько компонентов, запусти `project_scout` до правок.
  Передай ему конкретный вопрос и дождись его выводов.
  Применяй это правило и к задачам на исследование кода без изменений.
- Небольшие правки с понятной точкой входа выполняй напрямую.
- При изменении публичного контракта, схемы данных, формата сохранений,
  конкурентной логики или алгоритма симуляции передай план
  `project_reviewer` до правок. После реализации передай ему diff,
  критерии приёмки и результаты проверок.
- Запусти релевантные проверки проекта. Исправь подтверждённые проблемы
  и повтори затронутые проверки. Для обычных технических решений
  продолжай самостоятельно. Если требуется решение пользователя,
  доступ или разрешение, укажи конкретный блокер.
- Если нужный субагент недоступен, выполни проверку в основном треде
  и отметь это в итогах. Укажи, какие агенты реально участвовали
  и какие проверки прошли.
