# Testing

← [Documentation](../../README.md#documentation)

**A client through a container.** Register mocks before the tree is resolved; every consumer
then receives the mock:

```python
from unittest.mock import call

from nuke_di import Dependencies


async def test_greet() -> None:
    deps = Dependencies()
    db = deps.mock(Database)
    db.fetch_user.return_value = "alice"

    users = deps.resolve(UserService)
    async with deps:
        assert await users.greet(1) == "Hello, alice!"

    assert db.fetch_user.await_args_list == [call(1)]
```

**A client for one block, with `override()`.** `override(client_class, new=None)` registers a
Replacement like `mock()`, but it lasts until the end of the `with` block, even across several
`async with` cycles, and the container is flushed on exit, so nothing resolved with it leaks into
the next test. It works with the global `DI` too:

```python
# test_greet.py, with Database, UserService and handler from the Quick start
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_greet_with_fake() -> None:
    with DI.override(Database, FakeDatabase()):
        injected = DI.inject(handler)
        async with DI:
            print(await injected(1))

    print("after the block:", DI.clients)


async def test_greet_with_autospec() -> None:
    with DI.override(Database) as db:  # an autospec mock by default
        db.fetch_user.return_value = "bob"
        injected = DI.inject(handler)
        async with DI:
            print(await injected(2))

    db.fetch_user.assert_awaited_once_with(2)
```

The async tests in this section use [pytest-asyncio](https://pypi.org/project/pytest-asyncio/) with
`asyncio_mode = auto` in `pytest.ini`; without it pytest does not run `async def` tests.

```console
$ pytest -q -s test_greet.py
Hello, alice!
after the block: OrderedDict()
.Hello, bob!
.
2 passed in 0.01s
```

`database: connected` is never printed: a Replacement is not connected.

The rules:

- **Replace before you resolve.** A Replacement registered after `client_class` was resolved would
  reach only the consumers resolved later, while the earlier ones keep the real client, so `mock()`
  raises instead:

  ```python
  DI.inject(handler)  # resolves UserService -> Database
  DI.mock(Database)  # RuntimeError: Database is already resolved, call mock() before resolve() or inject()
  ```

- **`override()` starts from a container without resolved clients.** Flushing on exit would
  otherwise silently drop what was resolved before the block, so it raises
  `ConnectError: override(Database) needs a container without resolved clients, found: Database, UserService`.
  Call `DI.flush()` first or use the `global_di` fixture below.
- **One Replacement per class.** `mock(client_class)` called again returns the Replacement
  already registered; `mock(client_class, other)` and `override(client_class)` raise
  `ConnectError: Database already has a replacement`.
- **Replacements are not connected.** Their `connect()` / `disconnect()` are never called, and they
  do not take part in the [connect order](clients.md#connect-order).
- **How long a Replacement lasts.** One from `mock()` is dropped by the next `flush()`, including the
  one at the end of `disconnect()`: a test that connects the container more than once should use
  `override()`, whose Replacement survives every `flush()` until its block ends. An exception inside
  the block propagates unchanged; leaving the block normally while the container is still connected
  raises `RuntimeError`.
- **Nesting.** Blocks for different classes nest as long as each one opens before anything is
  resolved, e.g. `with DI.override(Database), DI.override(Clock):`; leaving the inner block keeps the
  outer Replacement.

**pytest fixtures.** Installing `nuke-di` registers a pytest plugin with two fixtures. Neither is
autouse, so existing tests run exactly as before:

| Fixture     | Gives                                                  |
|-------------|--------------------------------------------------------|
| `di`        | A fresh `Dependencies` for one test                    |
| `global_di` | The global `DI`, flushed before and after the test     |

A test that leaves the container connected gets an error at teardown, and the container is still
flushed, so the next test starts clean:

```python
# test_users.py, with Database, UserService and handler from the Quick start
from nuke_di import Dependencies


async def test_greet(di: Dependencies) -> None:
    di.mock(Database).fetch_user.return_value = "alice"
    users = di.resolve(UserService)
    async with di:
        assert await users.greet(1) == "Hello, alice!"


async def test_handler(global_di: Dependencies) -> None:  # e.g. code that calls DI.inject()
    global_di.mock(Database).fetch_user.return_value = "bob"
    injected = global_di.inject(handler)
    async with global_di:
        assert await injected(2) == "Hello, bob!"


async def test_forgets_to_disconnect(di: Dependencies) -> None:
    di.resolve(UserService)
    await di.connect()
```

```console
$ pytest -q test_users.py
...E                                                                     [100%]
==================================== ERRORS ====================================
_______________ ERROR at teardown of test_forgets_to_disconnect ________________
the test left the container of the "di" fixture connected; its clients were not disconnected, use `async with` or call disconnect()
----------------------------- Captured stdout call -----------------------------
database: connected
=========================== short test summary info ============================
ERROR test_users.py::test_forgets_to_disconnect - Failed: the test left the c...
3 passed, 1 error in 0.01s
```

The fixtures cannot disconnect a forgotten container themselves: by teardown the event loop of the
test may be closed. `global_di` only protects the tests that request it: a test that uses the global
`DI` without it can still leave clients behind for the next one. A project that defines its own
`di` fixture keeps it, since a `conftest.py` fixture wins over a plugin one;
`pytest -p no:nuke_di` turns the plugin off.

**An entrypoint, directly.** A normal import leaves the function available for a test.
For the [report example](workers-and-jobs.md#the-entrypoint-decorator), pass a mock for its client.

```python
from unittest.mock import create_autospec

from app.clients import Reports
from app.report import main


async def test_report() -> None:
    reports = create_autospec(Reports, instance=True)

    await main(reports)

    reports.publish.assert_awaited_once_with()
```

**Through a container.** Use a separate container to exercise the same wiring as the application.

```python
from nuke_di import Dependencies

from app.clients import Reports
from app.report import main


async def test_report_with_container() -> None:
    deps = Dependencies()
    reports = deps.mock(Reports)
    run_report = deps.inject(main)

    async with deps:
        await run_report()

    reports.publish.assert_awaited_once_with()
```

**An entrypoint wiring test.** `inject()` builds the tree without connecting anything. Check
the signatures of your executable modules in CI without starting a database.

```python
# test_wiring.py
from inspect import signature

from nuke_di import Dependencies

from app.report import main


def test_report_wiring() -> None:
    run_report = Dependencies().inject(main)
    signature(run_report).bind()
```

The [mypy plugin](clients.md#checking-the-tree-with-mypy) reports the same signature errors and
cycles without running anything; the test runs every `__init__` too, so it also catches one that raises.
