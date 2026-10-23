"""
The container resolves from several threads at once: one instance per singleton, no false cycles.
"""

import asyncio
import copy
import sys
import sysconfig
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from nuke_di import Client, ConnectError, Dependencies, NotSingletonClient
from tests.sequences import repeated

THREADS = 8
# Long enough for every other thread to reach resolve() while the first one is still inside an `__init__`
SLEEP = 0.01


class Slow(Client):
    """
    A singleton whose `__init__` takes long enough for other threads to ask for it meanwhile.
    """

    instances = 0

    def __init__(self) -> None:
        time.sleep(SLEEP)
        type(self).instances += 1


class Shared(Client):
    def __init__(self, slow: Slow) -> None:
        self.slow = slow


class ConsumerA(Client):
    def __init__(self, shared: Shared) -> None:
        self.shared = shared


class ConsumerB(Client):
    def __init__(self, shared: Shared) -> None:
        self.shared = shared


class PerConsumer(NotSingletonClient):
    def __init__(self, shared: Shared) -> None:
        self.shared = shared


def run_threads(targets: list[Callable[[], Any]]) -> list[Any]:
    """
    Run every target in a thread of its own, started together; the outcomes in the order of `targets`.
    """
    barrier = threading.Barrier(len(targets))
    outcomes: list[Any] = [None for _ in targets]

    def run(index: int, target: Callable[[], Any]) -> None:
        barrier.wait()
        try:
            outcomes[index] = target()
        except BaseException as exc:  # the thread reports what it raised
            outcomes[index] = exc

    threads = [threading.Thread(target=run, args=numbered) for numbered in enumerate(targets)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return outcomes


@pytest.fixture(autouse=True)
def count_instances() -> None:
    Slow.instances = 0


def test_threads_resolving_one_singleton_share_the_instance() -> None:
    deps = Dependencies()

    outcomes = run_threads(repeated([lambda: deps.resolve(ConsumerA)], THREADS))

    assert all(outcome is outcomes[0] for outcome in outcomes)
    assert Slow.instances == 1
    assert deps.connect_clients.count(outcomes[0].shared.slow) == 1
    assert deps.connect_clients.count(outcomes[0]) == 1
    assert len(deps.connect_clients) == 3


def test_threads_resolving_different_trees_see_no_false_cycle() -> None:
    """
    `Shared` is being resolved by one thread while another asks for it: without the lock the second thread would
    find it on the path of the first and report a cycle.
    """
    deps = Dependencies()

    targets = [
        lambda: deps.resolve(ConsumerA),
        lambda: deps.resolve(ConsumerB),
        lambda: deps.resolve(PerConsumer),
    ]
    outcomes = run_threads(repeated(targets, THREADS))

    assert not any(isinstance(outcome, Exception) for outcome in outcomes), outcomes
    shared = outcomes[0].shared
    assert all(outcome.shared is shared for outcome in outcomes)
    assert Slow.instances == 1


def test_threads_injecting_share_the_instance() -> None:
    deps = Dependencies()

    def handler_a(consumer: ConsumerA) -> ConsumerA:
        return consumer

    def handler_b(consumer: ConsumerB, per_consumer: PerConsumer) -> ConsumerB:
        return consumer

    targets = [
        lambda: deps.inject(handler_a)(),
        lambda: deps.inject(handler_b)(),
    ]
    outcomes = run_threads(repeated(targets, THREADS))

    assert not any(isinstance(outcome, Exception) for outcome in outcomes), outcomes
    shared = outcomes[0].shared
    assert all(outcome.shared is shared for outcome in outcomes)
    assert Slow.instances == 1


def test_client_resolving_on_its_own_reenters_the_lock() -> None:
    """
    A client's `__init__` that calls `inject()` or `resolve()` on the same container runs while that container is
    resolving it, in the same thread: the lock lets it in.
    """
    deps = Dependencies()

    class Reentrant(Client):
        def __init__(self) -> None:
            def handler(shared: Shared) -> Shared:
                return shared

            self.shared = deps.inject(handler)()
            self.slow = deps.resolve(Slow)

    targets = [lambda: deps.resolve(Reentrant), lambda: deps.resolve(ConsumerA)]
    outcomes = run_threads(repeated(targets, THREADS))

    assert not any(isinstance(outcome, Exception) for outcome in outcomes), outcomes
    reentrant = outcomes[0]
    assert isinstance(reentrant, Reentrant)
    assert reentrant.shared is outcomes[1].shared
    assert reentrant.slow is reentrant.shared.slow
    assert Slow.instances == 1


def test_override_block_does_not_hold_the_lock() -> None:
    """
    The user's code runs inside the block, so another thread resolves from the container meanwhile.
    """
    deps = Dependencies()
    resolved: list[ConsumerA] = []

    with deps.override(Slow) as slow:
        thread = threading.Thread(target=lambda: resolved.append(deps.resolve(ConsumerA)))
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive(), "resolve() blocked while the override block was open"

    assert resolved[0].shared.slow is slow
    assert Slow.instances == 0


def test_override_blocks_on_different_containers_are_independent() -> None:
    first = Dependencies()
    second = Dependencies()

    def use(deps: Dependencies) -> Slow:
        with deps.override(Slow) as slow:
            consumer = deps.resolve(ConsumerA)
            assert consumer.shared.slow is slow
            time.sleep(SLEEP)
            assert deps.resolve(ConsumerA) is consumer
            return slow

    targets = [lambda: use(first), lambda: use(second)]
    outcomes = run_threads(repeated(targets, THREADS))

    assert not any(isinstance(outcome, Exception) for outcome in outcomes), outcomes
    # No instance was made and neither container registered a client
    assert (Slow.instances, first.connect_clients, second.connect_clients) == (0, [], [])


def test_mock_from_threads_registers_one_replacement() -> None:
    deps = Dependencies()

    outcomes = run_threads(repeated([lambda: deps.mock(Slow)], THREADS))

    assert all(outcome is outcomes[0] for outcome in outcomes)
    assert deps.resolve(ConsumerA).shared.slow is outcomes[0]


def test_flush_from_a_thread_is_serialized_with_resolve() -> None:
    deps = Dependencies()

    outcomes = run_threads(
        [
            lambda: deps.resolve(ConsumerA),
            deps.flush,
            lambda: deps.resolve(ConsumerB),
        ],
    )

    assert not any(isinstance(outcome, Exception) for outcome in outcomes), outcomes
    # Whatever the order, every registered client is a complete tree
    for client in deps.connect_clients:
        if isinstance(client, ConsumerA | ConsumerB):
            assert client.shared in deps.connect_clients
            assert client.shared.slow in deps.connect_clients


def test_copy_of_a_container_has_a_lock_of_its_own() -> None:
    deps = Dependencies()
    deps.resolve(ConsumerA)

    copied = copy.deepcopy(deps)

    assert copied._lock is not deps._lock
    # The lock stays out of the comparison: two locks are never equal, two empty containers are
    assert copy.deepcopy(Dependencies()) == Dependencies()
    outcomes = run_threads(repeated([lambda: copied.resolve(ConsumerB)], THREADS))
    assert all(outcome is outcomes[0] for outcome in outcomes)
    assert copied.resolve(ConsumerA) is not deps.resolve(ConsumerA)


def test_connected_container_rejects_resolve_from_a_thread() -> None:
    deps = Dependencies()
    deps.resolve(ConsumerA)
    deps.connected = True

    outcomes = run_threads([lambda: deps.resolve(ConsumerB)])

    assert isinstance(outcomes[0], ConnectError)


async def test_connect_waits_for_a_resolve_in_flight() -> None:
    """
    connect() flips `connected` and takes its snapshot under the lock: a thread inside resolve() finishes first and
    its clients are connected, instead of being appended after the snapshot and left out.
    """
    deps = Dependencies()
    started = threading.Event()

    class Waits(Client):
        def __init__(self, slow: Slow) -> None:
            started.set()
            time.sleep(SLEEP)
            self.slow = slow

    thread = threading.Thread(target=deps.resolve, args=(Waits,))
    thread.start()
    started.wait()
    await deps.connect()
    thread.join()

    assert [type(client) for client in deps.connect_clients] == [Slow, Waits]
    assert [timing.connect_outcome for timing in deps.timings] == ["ok", "ok"]
    await deps.disconnect()


def test_resolve_refused_when_connect_flips_the_flag_while_it_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The race itself: resolve() passed its first check, then connect() took the lock and flipped `connected`
    before resolve() got it. Played deterministically by a lock that flips the flag as it is acquired.
    """
    deps = Dependencies()
    lock = deps._lock

    class FlipsOnAcquire:
        def __enter__(self) -> None:
            lock.__enter__()
            deps.connected = True

        def __exit__(self, *args: Any) -> None:
            lock.__exit__(*args)

    monkeypatch.setattr(deps, "_lock", FlipsOnAcquire())

    with pytest.raises(ConnectError, match="already connected"):
        deps.resolve(ConsumerA)
    assert not deps.connect_clients


async def test_resolve_after_a_concurrent_connect_is_refused() -> None:
    deps = Dependencies()
    deps.resolve(ConsumerA)
    loop = asyncio.get_running_loop()

    await deps.connect()
    outcomes = await loop.run_in_executor(None, run_threads, [lambda: deps.resolve(ConsumerB)])

    assert isinstance(outcomes[0], ConnectError)
    await deps.disconnect()


@pytest.mark.skipif(not sysconfig.get_config_var("Py_GIL_DISABLED"), reason="a build with the GIL")
def test_the_gil_is_off_on_a_free_threaded_build() -> None:
    # The free-threaded CI job runs with PYTHON_GIL=0: the threads of this module really run in parallel there
    # 3.13+: mypy and pyright check against 3.11
    assert not sys._is_gil_enabled()  # type: ignore[attr-defined, unused-ignore]  # pyright: ignore[reportAttributeAccessIssue]
