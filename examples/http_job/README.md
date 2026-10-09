# HTTP job

A `@job` that asks the PyPI JSON API for the latest version of several packages at once, through an
`httpx.AsyncClient` wrapped in a client. Use this shape for any job that talks to an HTTP API: the
HTTP client is created once with its base URL and timeout, shared by every request, and closed on exit.

| File               | What it holds                                                                |
|--------------------|------------------------------------------------------------------------------|
| `clients.py`       | `Settings`, which reads `PYPI_URL` and `HTTP_TIMEOUT_SECONDS`, and `PyPI`, which creates the `httpx.AsyncClient` in `connect()` and closes it in `disconnect()` |
| `versions.py`      | The `versions` job: `--package` repeated, the requests run in an `asyncio.TaskGroup` |
| `test_versions.py` | The job with `PyPI` mocked, and `PyPI` itself over an `httpx.MockTransport`; no network |

| Variable               | Default            |
|------------------------|--------------------|
| `PYPI_URL`             | `https://pypi.org` |
| `HTTP_TIMEOUT_SECONDS` | `5`                |

## Run

Needs network access; the versions are whatever PyPI says today:

```console
$ cd examples
$ uv run python -m http_job.versions --package fastapi --package litestar --package httpx
pypi: connected to https://pypi.org
fastapi: 0.143.0
litestar: 2.24.0
httpx: 0.28.1
pypi: disconnected
```

A failed request fails the run with exit code `1`, and the client is still closed. The traceback goes
to stderr; the commands below hide it, then keep only its key lines:

```console
$ uv run python -m http_job.versions -p fastapi -p no-such-package-nuke-di 2>/dev/null
pypi: connected to https://pypi.org
pypi: disconnected
$ echo $?
1
$ uv run python -m http_job.versions -p fastapi -p no-such-package-nuke-di 2>&1 >/dev/null | grep -E "failed|httpx\."
Run http_job.versions.versions failed
    | httpx.HTTPStatusError: Client error '404 Not Found' for url 'https://pypi.org/pypi/no-such-package-nuke-di/json'
$ HTTP_TIMEOUT_SECONDS=0.001 uv run python -m http_job.versions -p fastapi 2>&1 >/dev/null | grep -E "failed|httpx\."
Run http_job.versions.versions failed
    | httpx.ConnectTimeout
```

## Test

```console
$ uv run pytest -q http_job
...                                                                      [100%]
3 passed in 0.07s
```

## What to look at

- `PyPI.__init__` only stores `Settings`; the `httpx.AsyncClient` is created in `connect()` with an
  explicit `httpx.Timeout`, so no request can hang the job, and `aclose()` runs in `disconnect()`.
- `Settings` is a client too: the environment is read once, when the tree is built, and every client
  that needs it gets the same instance (see `settings/` for more).
- The `asyncio.TaskGroup` sends the requests concurrently and cancels the rest on the first failure;
  the error, wrapped in an `ExceptionGroup`, fails the run with exit code `1`.
- `PyPI.__init__` has a `transport` argument with a default: it is not a client, so the container
  leaves it alone, and a test builds `PyPI(Settings(), transport=httpx.MockTransport(...))` by hand,
  calling `connect()` / `disconnect()` itself.
- The job test uses `di.mock(PyPI)`: a Replacement is never connected, so no HTTP client is created.
