# Lifecycle guarantees

← [Documentation](../../README.md#documentation)

Build the required clients with `resolve()` or `inject()`, then enter `async with deps`.
The context manager calls `connect()` on entry and `disconnect()` on exit, including when
the body raises. You can call the same methods directly if an application owns the context.

## Ordering and failures

| Situation | Guarantee | Regression test |
|---|---|---|
| Startup | A client starts as soon as its dependencies finish connecting. Independent clients can connect concurrently. | [Dependency ordering](../../tests/test_connect_order.py), `test_dependency_connects_before_consumer` and `test_independent_clients_connect_concurrently` |
| Normal shutdown | A client disconnects after all its consumers finish. A Replacement participates in neither phase. | [Consumer ordering and Replacements](../../tests/test_connect_order.py), `test_dependency_disconnects_after_every_consumer` and `test_consumer_of_a_mock_waits_for_nothing` |
| A client fails during startup | The container cancels unfinished startup work, disconnects clients that connected, and clears its resolved clients before reporting the failure. | [Startup rollback](../../tests/test_connect_cleanup.py), `test_failure_disconnects_connected_clients` and `test_failure_resets_container` |
| A connect times out | `ConnectTimeoutError` reports the client whose connect took too long. Startup rolls back. | [Connect timeout](../../tests/test_errors.py), `test_connect_timeout` |
| A client cancels its own connect task | Startup fails with `ConnectError` and rolls back. Waiting consumers are cancelled without starting, including when the hook requests cancellation just before returning. | [Self-cancelled connect](../../tests/test_connect_cleanup.py), `test_self_cancelled_connect_rolls_back` and `test_pending_self_cancellation_prevents_starting_consumers` |
| The caller cancels startup | The container rolls back clients that connected and propagates cancellation. A new cancellation during rollback can interrupt cleanup. | [Cancelled startup and rollback](../../tests/test_connect_cleanup.py), `test_cancellation_disconnects_connected_clients` and `test_cancelled_rollback_flushes` |
| A disconnect fails, times out or cancels its own task | The failure is logged. Dependencies can still disconnect. | [Disconnect failure and timeout](../../tests/test_connect_order.py), `test_failed_disconnect_still_releases_its_dependencies` and `test_timed_out_disconnect_still_releases_its_dependencies`. [Self-cancelled disconnect](../../tests/test_connect_cleanup.py), `test_self_cancelled_disconnect_releases_dependencies` |
| The disconnect deadline expires | A disconnect still in progress is timed out. Clients that have yet to start are skipped and named in the log. The same deadline applies during startup rollback. | [Disconnect and rollback deadlines](../../tests/test_connect_order.py), `test_deadline_bounds_a_chain_of_hung_disconnects` and `test_deadline_bounds_the_rollback_of_a_failed_connect` |
| The caller cancels shutdown | Cancellation propagates and the container clears its resolved clients. Some clients may have been skipped. | [Cancelled shutdown](../../tests/test_connect_cleanup.py), `test_cancelled_disconnect_waits_for_the_clients_disconnecting_and_flushes` |

The [timeout settings](configuration.md) bound each client operation and the overall
shutdown. Clients should cooperate with cancellation. A client whose startup fails partway
through is responsible for releasing resources it acquired during that call.

## When clients are available

`connected` is true from the start of connection through the end of disconnection. An
already resolved client can be looked up during startup and while connected. During
disconnection, `resolve()` raises `RuntimeError` even for a previously resolved client.
Once disconnection clears the resolved clients, build and bind the clients for the next run
before starting it. See [The container](container.md) for the rules for `inject()`.

## Entry points and framework lifespans

`@entrypoint` injects the function's clients and runs the function inside the container's
context. Importing the module keeps the function available for direct calls.
[The executable-module tests](../../tests/test_entrypoint.py) run the module in a subprocess.

Framework adapters connect the container before the application's own startup work and
disconnect it after the application's shutdown work. A failing connection prevents app
startup. ASGI and FastAPI exercise these guarantees in
[the ASGI lifespan tests](../../tests/test_asgi.py) and
[the FastAPI lifecycle tests](../../tests/test_fastapi.py).
The application or framework owns process signals and its background tasks.
