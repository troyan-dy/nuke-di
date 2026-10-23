import asyncio

from nuke_di import ConnectError, Dependencies

from startup_failures.clients import Orders


async def main() -> None:
    deps = Dependencies()
    deps.resolve(Orders)
    try:
        await deps.connect()
    except ConnectError as exc:
        print(f"{type(exc).__name__}: {exc}")
        print(f"__cause__: {exc.__cause__!r}")
    print("connected:", deps.connected)
    for timing in deps.timings:
        print(f"{timing.name:<8} connect {timing.connect_outcome}  disconnect {timing.disconnect_outcome}")


if __name__ == "__main__":
    asyncio.run(main())
