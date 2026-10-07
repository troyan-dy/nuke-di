from nuke_di import Client, Dependencies, NotSingletonClient


class InnerDeps(NotSingletonClient):
    def number(self) -> int:
        return 1


class PublicClient(Client):
    def __init__(self, inner: InnerDeps):
        self.inner = inner

    async def add(self, a: int, b: int) -> int:
        return a + b


async def any_func(a: int, adder: PublicClient, inner: InnerDeps) -> int:
    assert adder.inner is not inner

    return await adder.add(a, inner.number())


async def test_inject() -> None:
    dep = Dependencies()
    new_func = dep.inject(any_func)

    async with dep:
        assert (await new_func(1)) == 2
