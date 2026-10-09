# <a id="the-container"></a>容器

[English](../../guide/container.md) · [Русский](../ru/container.md) · **简体中文** · [Español](../es/container.md) · [Português (Brasil)](../pt-BR/container.md) · [日本語](../ja/container.md) · [Polski](../pl/container.md)

← [文档](../README.zh-CN.md#documentation)

`Dependencies` 就是容器。`DI` 是一个开箱即用的全局实例；需要隔离时（例如在测试中），
可以创建自己的实例。

| 方法                 | 说明                                                                    |
|----------------------|-------------------------------------------------------------------------|
| `resolve(cls)`       | 构建 `cls` 及其依赖树。对 `Client` 是幂等的。                           |
| `inject(func)`       | 返回已绑定客户端参数的 `functools.partial(func, ...)`。除 `*args` / `**kwargs` 外，`func` 的每个参数都必须有类型提示。 |
| `connect()`          | 逐层对每个已解析的客户端调用 `connect()`。                              |
| `disconnect()`       | 逐层逆序调用 `disconnect()`，然后对容器执行 `flush()`。                 |
| `async with`         | 进入时调用 `connect()`，退出时调用 `disconnect()`。                     |
| `mock(cls, new=None)`| 为 `cls` 注册一个替换对象（默认为 autospec mock），有效期到下一次 `flush()` 为止。必须在 `cls` 被解析之前调用。 |
| `override(cls, new=None)` | 仅在 `with` 块内有效的替换对象，块结束后执行 `flush()`；参见[测试](testing.md)。 |
| `flush()`            | 丢弃所有已解析的客户端。                                                |
| `timings`            | 最近一次 `connect()` 的每个客户端一个 `ClientTiming`；见[启动耗时](clients.md#startup-timings)。 |
| `graph()`            | 已解析客户端的 `Graph`，含依赖和层，带 `to_mermaid()`；见[依赖图](clients.md#the-graph)。 |

`inject()` 的结果保留函数的返回类型，但其余参数不带类型：类型检查器无法从签名中减去客户端参数。

`resolve`、`inject`、`mock`、`override` 和 `flush` 只能在容器未连接时使用：
整棵树在启动之前就已构建完成。

```python
async def main() -> None:
    deps = Dependencies()
    injected = deps.inject(handler)  # build the tree
    async with deps:  # connect
        await injected(42)
        deps.resolve(Cache)  # ConnectError: resolve(Cache): the container is already connected; ...
```

容器可以安全地从多个线程解析：每个容器一把锁串行化 `resolve`、`inject`、`mock`、`override` 和 `flush`，因此两个线程同时请求的单例只构建一次。`connect()` 和 `disconnect()` 属于同一个事件循环。

```python
import threading

from nuke_di import Client, Dependencies


class Postgres(Client):
    instances = 0

    def __init__(self) -> None:
        type(self).instances += 1


class Orders(Client):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg


deps = Dependencies()
threads = [threading.Thread(target=deps.resolve, args=(Orders,)) for _ in range(8)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
print("instances:", Postgres.instances, "clients:", len(deps.connect_clients))
```

```text
instances: 1 clients: 2
```
