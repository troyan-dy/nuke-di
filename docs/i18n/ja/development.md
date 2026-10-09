# <a id="development"></a>開発

[English](../../guide/development.md) · [Русский](../ru/development.md) · [简体中文](../zh-CN/development.md) · [Español](../es/development.md) · [Português (Brasil)](../pt-BR/development.md) · **日本語** · [Polski](../pl/development.md)

← [ドキュメント](../README.ja.md#documentation)

```bash
make install   # uv sync --locked
make check     # ruff, mypy, pyright and tests, as in CI
make cov       # tests with a coverage report (terminal + htmlcov/)
make test-all  # tests on Python 3.11-3.14
```

行カバレッジとブランチカバレッジはいずれも 100% で、これを下回ると CI が失敗します（`pyproject.toml` の `fail_under = 100`）。

## <a id="releases"></a>リリース

`master` へのマージは、すべてリリースになります。`Release` ワークフローは `pyproject.toml` に書かれたバージョンを PyPI に公開し、`vX.Y.Z` のタグを付け、`CHANGELOG.md` の該当セクションから GitHub リリースを作成します。したがって、プルリクエストはそれぞれ自身のバージョンを持ちます。`uv version --bump patch|minor|major` でバージョンを上げ、`## [Unreleased]` を `## [X.Y.Z] - YYYY-MM-DD` に書き換えて、末尾に比較リンクを追加してください。CI はすべてのプルリクエストでこれを確認します。ローカルでは `make check-version` で確認できます。

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

新しいバージョンなしで `master` に到達した変更（直接プッシュされた場合など）は、何かがビルド・公開される前に `Release` ワークフローで失敗します。
