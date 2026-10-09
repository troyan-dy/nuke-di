# <a id="development"></a>Desarrollo

[English](../../guide/development.md) · [Русский](../ru/development.md) · [简体中文](../zh-CN/development.md) · **Español** · [Português (Brasil)](../pt-BR/development.md) · [日本語](../ja/development.md) · [Polski](../pl/development.md)

← [Documentación](../README.es.md#documentation)

```bash
make install   # uv sync --locked
make check     # ruff, mypy, pyright and tests, as in CI
make cov       # tests with a coverage report (terminal + htmlcov/)
make test-all  # tests on Python 3.11-3.14
```

La cobertura de líneas y de ramas es del 100 %, y la CI falla si baja de ese valor
(`fail_under = 100` en `pyproject.toml`).

## <a id="releases"></a>Publicación de versiones

Cada merge en `master` es una nueva versión publicada. El workflow `Release` publica en PyPI la versión
indicada en `pyproject.toml`, crea la etiqueta `vX.Y.Z` y crea una release de GitHub a partir de su sección de
`CHANGELOG.md`. Por eso cada pull request lleva su propia versión: súbela con `uv version --bump
patch|minor|major` y convierte `## [Unreleased]` en `## [X.Y.Z] - YYYY-MM-DD`, con un enlace de comparación al
final. La CI lo comprueba en cada pull request, y `make check-version` lo comprueba en local:

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

Un cambio que llega a `master` sin una nueva versión, por ejemplo con un push directo, hace fallar el workflow
`Release` antes de que se construya o se publique nada.
