# <a id="development"></a>开发

[English](../../guide/development.md) · [Русский](../ru/development.md) · **简体中文** · [Español](../es/development.md) · [Português (Brasil)](../pt-BR/development.md) · [日本語](../ja/development.md) · [Polski](../pl/development.md)

← [文档](../README.zh-CN.md#documentation)

```bash
make install   # uv sync --locked
make check     # ruff, mypy, pyright and tests, as in CI
make cov       # tests with a coverage report (terminal + htmlcov/)
make test-all  # tests on Python 3.11-3.14
```

行覆盖率和分支覆盖率均为 100%，一旦低于这个值 CI 就会失败
（`pyproject.toml` 中的 `fail_under = 100`）。

## <a id="releases"></a>发布

每次合并到 `master` 就是一次发布。`Release` workflow 会把 `pyproject.toml` 中的版本
发布到 PyPI，打上 `vX.Y.Z` 标签，并根据 `CHANGELOG.md` 中对应的章节创建 GitHub Release。
因此，每个 pull request 都要自带版本号：用 `uv version --bump patch|minor|major` 提升版本，
并把 `## [Unreleased]` 改为 `## [X.Y.Z] - YYYY-MM-DD`，同时在文件底部添加 compare 链接。
CI 会在每个 pull request 上检查这一点，本地则可以用 `make check-version` 检查：

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

没有提升版本号就进入 `master` 的变更（例如直接 push 的提交），会让 `Release`
workflow 在构建或发布任何东西之前就失败。
