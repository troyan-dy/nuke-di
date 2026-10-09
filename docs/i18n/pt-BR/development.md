# <a id="development"></a>Desenvolvimento

[English](../../guide/development.md) · [Русский](../ru/development.md) · [简体中文](../zh-CN/development.md) · [Español](../es/development.md) · **Português (Brasil)** · [日本語](../ja/development.md) · [Polski](../pl/development.md)

← [Documentação](../README.pt-BR.md#documentation)

```bash
make install   # uv sync --locked
make check     # ruff, mypy, pyright and tests, as in CI
make cov       # tests with a coverage report (terminal + htmlcov/)
make test-all  # tests on Python 3.11-3.14
```

A cobertura de linhas e de branches é de 100%, e o CI falha se ela cair abaixo disso
(`fail_under = 100` no `pyproject.toml`).

## <a id="releases"></a>Releases

Todo merge em `master` é uma release. O workflow `Release` publica no PyPI a versão definida no
`pyproject.toml`, cria a tag `vX.Y.Z` e gera uma release no GitHub a partir da seção correspondente do
`CHANGELOG.md`. Por isso, cada pull request traz a sua própria versão: aumente-a com `uv version --bump
patch|minor|major` e transforme `## [Unreleased]` em `## [X.Y.Z] - YYYY-MM-DD`, com um link de comparação
no final do arquivo. O CI verifica isso em todo pull request, e `make check-version` faz a mesma verificação localmente:

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

Uma mudança que chega à `master` sem uma versão nova, por exemplo com um push direto, faz o workflow `Release`
falhar antes que qualquer coisa seja construída ou publicada.
