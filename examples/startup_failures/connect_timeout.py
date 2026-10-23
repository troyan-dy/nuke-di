import asyncio

from nuke_di import ConnectTimeoutError, Dependencies

from startup_failures.clients import Reports


async def main() -> None:
    deps = Dependencies()  # reads CONNECT_TIMEOUT_SECONDS, 30 by default
    deps.resolve(Reports)
    try:
        await deps.connect()
    except ConnectTimeoutError as exc:  # a ConnectError too
        print(f"{type(exc).__name__}: {exc}")
        print(f"__cause__: {exc.__cause__!r}")
    for timing in deps.timings:
        print(f"{timing.name:<8} connect {timing.connect_outcome}  disconnect {timing.disconnect_outcome}")


if __name__ == "__main__":
    asyncio.run(main())
