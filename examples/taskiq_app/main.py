import asyncio

from taskiq_app.tasks import broker, send_report, send_reports


async def main() -> None:
    # An InMemoryBroker is its own worker: its startup connects the clients, its shutdown disconnects them
    await broker.startup()
    try:
        report = await send_report.kiq(1)
        print("send_report(1): sent to", (await report.wait_result()).return_value)

        reports = await send_reports.kiq([2, 3])
        print("send_reports([2, 3]):", (await reports.wait_result()).return_value, "reports")
    finally:
        await broker.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
