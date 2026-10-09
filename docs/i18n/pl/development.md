# <a id="development"></a>Rozwój

[English](../../guide/development.md) · [Русский](../ru/development.md) · [简体中文](../zh-CN/development.md) · [Español](../es/development.md) · [Português (Brasil)](../pt-BR/development.md) · [日本語](../ja/development.md) · **Polski**

← [Dokumentacja](../README.pl.md#documentation)

```bash
make install   # uv sync --locked
make check     # ruff, mypy, pyright and tests, as in CI
make cov       # tests with a coverage report (terminal + htmlcov/)
make test-all  # tests on Python 3.11-3.14
```

Pokrycie linii i gałęzi wynosi 100%, a CI kończy się błędem, jeśli spadnie poniżej tego poziomu
(`fail_under = 100` w `pyproject.toml`).

## <a id="releases"></a>Wydania

Każdy merge do `master` to nowe wydanie. Workflow `Release` publikuje w PyPI wersję z
`pyproject.toml`, oznacza ją tagiem `vX.Y.Z` i tworzy release na GitHubie z odpowiadającej jej sekcji
`CHANGELOG.md`. Dlatego każdy pull request niesie własną wersję: podnieś ją przez `uv version --bump
patch|minor|major` i zamień `## [Unreleased]` na `## [X.Y.Z] - YYYY-MM-DD`, dodając na końcu pliku
link do porównania. CI sprawdza to w każdym pull requeście, a lokalnie robi to `make check-version`:

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

Zmiana, która trafi do `master` bez nowej wersji, np. wypchnięta bezpośrednio, kończy workflow
`Release` błędem, zanim cokolwiek zostanie zbudowane lub opublikowane.
