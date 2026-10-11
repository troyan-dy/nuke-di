# Development

← [Documentation](../../README.md#documentation)

```bash
make install   # uv sync --locked
make check     # ruff, mypy, pyright and tests, as in CI
make cov       # tests with a coverage report (terminal + htmlcov/)
make test-all  # tests on Python 3.11-3.14
```

Line and branch coverage is 100%, and CI fails if it drops below that
(`fail_under = 100` in `pyproject.toml`).

## Releases

Every merge into `master` is a release. The `Release` workflow publishes the version in
`pyproject.toml` to PyPI, tags it `vX.Y.Z` and creates a GitHub release from its section of
`CHANGELOG.md`. A pull request therefore carries its own version: raise it with `uv version --bump
patch|minor|major` and turn `## [Unreleased]` into `## [X.Y.Z] - YYYY-MM-DD` with a compare link at
the bottom. CI checks this on every pull request, and `make check-version` checks it locally:

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

A change that reaches `master` without a new version, e.g. pushed directly, fails the `Release`
workflow before anything is built or published.
