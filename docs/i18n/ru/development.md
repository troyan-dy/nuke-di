# <a id="development"></a>Разработка

[English](../../guide/development.md) · **Русский** · [简体中文](../zh-CN/development.md) · [Español](../es/development.md) · [Português (Brasil)](../pt-BR/development.md) · [日本語](../ja/development.md) · [Polski](../pl/development.md)

← [Документация](../README.ru.md#documentation)

```bash
make install   # uv sync --locked
make check     # ruff, mypy, pyright and tests, as in CI
make cov       # tests with a coverage report (terminal + htmlcov/)
make test-all  # tests on Python 3.11-3.14
```

Покрытие строк и ветвлений — 100%, и CI падает, если оно опускается ниже
(`fail_under = 100` в `pyproject.toml`).

## <a id="releases"></a>Релизы

Каждый мерж в `master` — это релиз. Workflow `Release` публикует версию из
`pyproject.toml` в PyPI, ставит тег `vX.Y.Z` и создаёт релиз на GitHub из её раздела в
`CHANGELOG.md`. Поэтому pull request несёт собственную версию: поднимите её командой `uv version --bump
patch|minor|major` и превратите `## [Unreleased]` в `## [X.Y.Z] - YYYY-MM-DD` со ссылкой на сравнение
внизу файла. CI проверяет это в каждом pull request, а локально — `make check-version`:

```console
$ make check-version
git fetch --quiet --tags origin master
uv run --no-project python scripts/version.py check origin/master
error: version 1.5.0 is not above 1.5.0 on master: bump it, e.g. `uv version --bump minor`
error: v1.5.0 is released already
make: *** [check-version] Error 1

$ uv version --bump patch
...
nuke-di 1.5.0 => 1.5.1
$ make check-version
git fetch --quiet --tags origin master
uv run --no-project python scripts/version.py check origin/master
1.5.1
```

Изменение, попавшее в `master` без новой версии, например запушенное напрямую, роняет workflow
`Release` ещё до сборки и публикации.
