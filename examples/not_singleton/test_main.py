from nuke_di import Dependencies

from not_singleton.main import HttpSession, Orders, Payments, Settings


def test_settings_is_shared_and_sessions_are_not(di: Dependencies) -> None:
    orders, payments = di.resolve(Orders), di.resolve(Payments)

    assert orders.settings is payments.settings is di.resolve(Settings)
    assert orders.http is not payments.http
    assert orders.http.settings is orders.settings  # a NotSingletonClient still gets the shared singletons
    assert di.resolve(HttpSession) is not orders.http  # every resolve() builds a new one


async def test_sessions_keep_their_own_state(di: Dependencies) -> None:
    orders, payments = di.resolve(Orders), di.resolve(Payments)
    async with di:
        await orders.recent()
        await orders.recent()
        await payments.balance()

    assert (orders.http.requests, payments.http.requests) == (2, 1)
    assert orders.http.headers == {"X-Caller": "orders"}
    assert payments.http.headers == {"X-Caller": "payments"}
