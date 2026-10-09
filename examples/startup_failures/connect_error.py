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
    for t in deps.timings:
        print(f"{t.name:<8} layer {t.layer}  connect {t.connect_outcome}  disconnect {t.disconnect_outcome}")


if __name__ == "__main__":
    asyncio.run(main())
