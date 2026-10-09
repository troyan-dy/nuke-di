# <a id="clients"></a>客户端

[English](../../guide/clients.md) · [Русский](../ru/clients.md) · **简体中文** · [Español](../es/clients.md) · [Português (Brasil)](../pt-BR/clients.md) · [日本語](../ja/clients.md) · [Polski](../pl/clients.md)

← [文档](../README.zh-CN.md#documentation)

## <a id="client-and-notsingletonclient"></a>Client 与 NotSingletonClient

每个依赖都是以下两个基类之一的子类：

| 基类                 | 实例                                               |
|----------------------|----------------------------------------------------|
| `Client`             | 单例：每个容器一个实例                             |
| `NotSingletonClient` | 每个声明它的使用方都会得到一个新实例               |

```python
from nuke_di import Client, Dependencies, NotSingletonClient


class Settings(Client):
    pass


class HttpSession(NotSingletonClient):
    pass


class Orders(Client):
    def __init__(self, settings: Settings, http: HttpSession) -> None:
        self.settings = settings
        self.http = http


class Payments(Client):
    def __init__(self, settings: Settings, http: HttpSession) -> None:
        self.settings = settings
        self.http = http


deps = Dependencies()
orders = deps.resolve(Orders)
payments = deps.resolve(Payments)

print(orders.settings is payments.settings)  # one Settings for the whole container
print(orders.http is payments.http)  # every consumer gets its own HttpSession
print(deps.resolve(Orders) is orders)  # resolve() is idempotent for a Client
```

```text
True
False
True
```

客户端通过带注解的 `__init__` 参数声明自己的依赖。只有注解为客户端类型的参数才会被注入，
并且解析是递归进行的。

客户端的生命周期与其容器相同。没有按请求或按消息创建的客户端，今后也不会有
（[ADR-0006](../../adr/0006-clients-live-as-long-as-the-container.md)）：事务以及其他只存在于一次请求内的东西，由处理函数通过客户端的方法打开。
`NotSingletonClient` 目前仍受支持，但会在未来的某个主版本中移除，新代码不要建立在它之上。

## <a id="connect-and-disconnect"></a>connect() 与 disconnect()

重写异步方法 `connect()` / `disconnect()`，用来打开和释放连接池之类的资源。`__init__`
只负责保存依赖，任何涉及 I/O 的操作都应放在 `connect()` 中：

```python
class Redis(Client):
    def __init__(self) -> None:
        self._pool: Pool | None = None

    async def connect(self) -> None:
        self._pool = await create_pool()

    async def disconnect(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
```

每次 `connect()` 受 `CONNECT_TIMEOUT_SECONDS`（默认 `30`）限制，每次 `disconnect()` 受
`DISCONNECT_TIMEOUT_SECONDS`（默认 `10`）限制。失败或卡住的 `disconnect()` 会被记录到日志，
其他客户端仍会照常关闭。

## <a id="dataclass-clients"></a>dataclass 客户端

`client_dataclass` 把一个类同时变成 `Client` 和 dataclass，于是它的字段就成了被注入的依赖。同时也要继承 `Client`：装饰器的类型是恒等的，所以让 mypy 和 pyright 知道 `Checkout` 是客户端的正是这个基类；没有它，这个类只在运行时才是客户端：

```python
from nuke_di import Client, Dependencies, client_dataclass


class Postgres(Client):
    pass


class Payments(Client):
    pass


@client_dataclass(frozen=True)
class Checkout(Client):
    pg: Postgres
    payments: Payments


checkout = Dependencies().resolve(Checkout)
print(checkout)
print(isinstance(checkout, Client))
```

```text
Checkout(pg=<__main__.Postgres object at 0x...>, payments=<__main__.Payments object at 0x...>)
True
```

它接受与 `dataclasses.dataclass` 相同的关键字参数。

## <a id="layers"></a>层

客户端按层并发连接。没有依赖的客户端构成第 0 层；其他客户端位于它最高的那个依赖所在层的上一层。
前一层全部连接完成后，下一层才会开始，因此客户端绝不会先于自己的依赖连接。
`disconnect()` 则按相反顺序遍历各层。

```python
import asyncio
import logging

from nuke_di import Client, Dependencies

logging.basicConfig(level=logging.DEBUG, format="%(message)s")
logging.getLogger("asyncio").setLevel(logging.WARNING)  # keep only the nuke_di records


class Postgres(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.2)
        print("  postgres ready")


class Redis(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.1)
        print("  redis ready")


class Payments(Client):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg


class Checkout(Client):
    def __init__(self, pg: Postgres, redis: Redis, payments: Payments) -> None:
        self.pg, self.redis, self.payments = pg, redis, payments


async def main() -> None:
    deps = Dependencies()
    deps.resolve(Checkout)
    async with deps:
        print("-- application is running --")


asyncio.run(main())
```

`nuke_di` logger 的 `DEBUG` 日志展示了各层：

```text
Resolving dependency "Checkout"
Resolving dependency "Postgres"
Resolving dependency "Redis"
Resolving dependency "Payments"
Connecting layer 0: Postgres, Redis
Connecting client Postgres
Connecting client Redis
  redis ready
Connected client Redis in 0.101s
  postgres ready
Connected client Postgres in 0.201s
Connecting layer 1: Payments
Connecting client Payments
Connected client Payments in 0.000s
Connecting layer 2: Checkout
Connecting client Checkout
Connected client Checkout in 0.000s
Connected 4 clients in 3 layers in 0.20s (slowest: Postgres 0.20s, Redis 0.10s, Payments 0.00s)
-- application is running --
Disconnecting client Checkout
Disconnected client Checkout in 0.000s
Disconnecting client Payments
Disconnected client Payments in 0.000s
Disconnecting client Postgres
Disconnected client Postgres in 0.000s
Disconnecting client Redis
Disconnected client Redis in 0.000s
```

```text
Checkout(pg, redis, payments)    layer 2
Payments(pg)                     layer 1
Postgres, Redis                  layer 0  <- connect together, in 0.2s rather than 0.3s
```

只有在 `__init__` 中声明的依赖才参与排序。如果某个客户端需要另一个客户端先连接好，就把它声明为依赖。
设置 `CONNECT_CONCURRENCY` 可以限制同时连接的客户端数量。

## <a id="startup-timings"></a>启动耗时

容器会测量每个客户端的 `connect()` 和 `disconnect()`，因此启动变慢时能直接找到原因。
`connect()` 成功后，它会以 `INFO` 级别输出一条汇总；对于耗时超过 `CONNECT_TIMEOUT_SECONDS`
一半的客户端，还会输出一条 `WARNING`，远在该客户端开始因超时而失败之前：

```python
# startup.py
import asyncio
import logging

from nuke_di import Client, Dependencies, DependenciesSettings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


class Postgres(Client):
    async def connect(self) -> None:
        await asyncio.sleep(0.2)


class Kafka(Client):
    async def connect(self) -> None:
        await asyncio.sleep(1.6)

    async def disconnect(self) -> None:
        await asyncio.sleep(0.3)


class Orders(Client):
    def __init__(self, pg: Postgres, kafka: Kafka) -> None:
        self.pg, self.kafka = pg, kafka


async def main() -> None:
    deps = Dependencies(settings=DependenciesSettings(connect_timeout=3))
    deps.resolve(Orders)
    async with deps:
        print("-- application is running --")

    for t in deps.timings:
        print(
            f"{t.name:<8} layer {t.layer}  connect {t.connect:.2f}s {t.connect_outcome:<3}  "
            f"disconnect {t.disconnect:.2f}s {t.disconnect_outcome}"
        )


asyncio.run(main())
```

```console
$ python startup.py
INFO Connected 3 clients in 2 layers in 1.60s (slowest: Kafka 1.60s, Postgres 0.20s, Orders 0.00s)
WARNING Client Kafka took 1.60s to connect, more than half of CONNECT_TIMEOUT_SECONDS (3s)
-- application is running --
Postgres layer 0  connect 0.20s ok   disconnect 0.00s ok
Kafka    layer 0  connect 1.60s ok   disconnect 0.30s ok
Orders   layer 1  connect 0.00s ok   disconnect 0.00s ok
```

`deps.timings` 按连接顺序为最近一次 `connect()` 的每个客户端保存一个 `ClientTiming`。
它在 `disconnect()` 之后依然保留，因此可以在容器停止后读取。在 FastAPI 应用中，传给 `FastAPI()`
的 lifespan 运行在已连接的容器内部，因此能看到连接耗时。
worker 或 job 会在 [`Run.clients`](workers-and-jobs.md#startup-metrics-and-structured-logs) 中得到同一个列表。

| `ClientTiming` 字段  | 值 |
|----------------------|----|
| `name`               | 客户端的类名 |
| `layer`              | 客户端所在的[层](#layers) |
| `connect`            | 在 `connect()` 中花费的秒数，不含等待 `CONNECT_CONCURRENCY` 的时间；`connect()` 从未运行时为 `None` |
| `connect_outcome`    | `"ok"`、`"failed"`、`"timed_out"`、`"cancelled"`；`connect()` 从未开始时为 `None` |
| `disconnect`、`disconnect_outcome` | `disconnect()` 的对应值；客户端断开之前为 `None` |

当某个客户端连接失败时，同一层中仍在连接的客户端为 `"cancelled"`，更高的层保持 `None`，
已经连接的客户端会被回滚，因此会得到 `disconnect_outcome`。库只负责测量：把耗时导出为
指标或 span 由你的代码完成。

## <a id="the-graph"></a>依赖图

依赖图只存在于运行中的进程里：上面的 `DEBUG` 日志是唯一能看到一个入口点拉入哪些客户端、
每个客户端在哪一层连接的地方。`graph()` 把同一幅图作为数据返回，在 `connect()` 之前或之后都可以。
下面是[层](#layers)一节示例中的客户端，去掉了它们的 `connect()`：

```python
# graph.py
from nuke_di import Client, Dependencies


class Postgres(Client):
    pass


class Redis(Client):
    pass


class Payments(Client):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg


class Checkout(Client):
    def __init__(self, pg: Postgres, redis: Redis, payments: Payments) -> None:
        self.pg, self.redis, self.payments = pg, redis, payments


deps = Dependencies()
deps.resolve(Checkout)
nodes = {node.name: node for node in deps.graph().nodes}
for node in nodes.values():
    print(f"{node.name:<8} layer {node.layer}  needs {list(node.dependencies)}")
print("shared:", nodes["Checkout"].dependencies["pg"] is nodes["Payments"].dependencies["pg"])
print(deps.graph().to_mermaid())
```

```console
$ python graph.py
Postgres layer 0  needs []
Redis    layer 0  needs []
Payments layer 1  needs ['pg']
Checkout layer 2  needs ['pg', 'redis', 'payments']
shared: True
graph BT
  subgraph layer0 [layer 0]
    Postgres
    Redis
  end
  subgraph layer1 [layer 1]
    Payments
  end
  subgraph layer2 [layer 2]
    Checkout
  end
  Postgres --> Payments
  Postgres --> Checkout
  Redis --> Checkout
  Payments --> Checkout
```

GitHub 会在 README、pull request 或 issue 中渲染 Mermaid 文本，所以项目无需运行进程就能展示自己的架构：

```mermaid
graph BT
  subgraph layer0 [layer 0]
    Postgres
    Redis
  end
  subgraph layer1 [layer 1]
    Payments
  end
  subgraph layer2 [layer 2]
    Checkout
  end
  Postgres --> Payments
  Postgres --> Checkout
  Redis --> Checkout
  Payments --> Checkout
```

`Graph.nodes` 为每个已解析的客户端保存一个 `Node`，按解析顺序排列，因此客户端排在它的依赖之后。
它是一个快照：`flush()` 会清空它，但仍然打开的 `override()` 块的 Replacement 会留下，它们经得起任何一次 `flush()`。

| `Node` 字段    | 值 |
|----------------|----|
| `name`         | 客户端的类名 |
| `cls`          | 使用方请求的类 |
| `singleton`    | `Client` 为 `True`，`NotSingletonClient` 为 `False` |
| `layer`        | 客户端的[层](#layers)；Replacement 为 `None`，它永远不会连接 |
| `replacement`  | 通过 `mock()` 或 `override()` 注册、代替 `cls` 的对象；真实客户端为 `None` |
| `dependencies` | `__init__` 各参数对应的客户端，按参数名索引 |

`NotSingletonClient` 的每个实例各占一个节点，名字相同；`to_mermaid()` 从第二个开始编号
（`Session`、`Session_2`）。Replacement 画在层之外，带虚线边框，并标出代替它的对象名：`Postgres: AsyncMock`。
节点按标识比较，因此上面的 `shared: True` 说明 `Checkout` 和 `Payments` 拿到的是同一个 `Postgres`。

## <a id="when-a-client-fails-to-connect"></a>客户端连接失败时

如果某个客户端连接失败，同一层的其余客户端会被取消，后续各层也不会启动。已经连接的客户端会按层逆序断开连接，
容器最终处于未连接且为空的状态：

```python
import asyncio

from nuke_di import Client, ConnectError, Dependencies


class Postgres(Client):
    async def connect(self) -> None:
        print("postgres: connected")

    async def disconnect(self) -> None:
        print("postgres: disconnected")


class Kafka(Client):
    async def connect(self) -> None:
        raise OSError("broker kafka-1:9092 is unreachable")


class Orders(Client):
    def __init__(self, pg: Postgres, kafka: Kafka) -> None:
        self.pg, self.kafka = pg, kafka


async def main() -> None:
    deps = Dependencies()
    deps.resolve(Orders)
    try:
        await deps.connect()
    except ConnectError as exc:
        print(f"{exc} <- {exc.__cause__!r}")
    print("connected:", deps.connected)


asyncio.run(main())
```

```text
postgres: connected
Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable
Traceback (most recent call last):
  ...
OSError: broker kafka-1:9092 is unreachable
postgres: disconnected
Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable <- OSError('broker kafka-1:9092 is unreachable')
connected: False
```

`connect()` 本身被取消时也会执行同样的清理。`ConnectError` 继承自 `SystemExit`，因此不捕获它的应用会直接停止——
当某个依赖不可用时，这通常正是你想要的。被 mock 的客户端不会被连接，也不影响分层。

## <a id="when-the-tree-cannot-be-built"></a>依赖树无法构建时

解析会在调用每个 `__init__` 之前先对它做检查，因此无法构建的客户端会在任何连接发生之前就失败，
错误信息会指出有问题的参数，以及从你所请求的客户端出发的路径：

```python
from typing import Protocol

from nuke_di import Client, Dependencies, InvalidSignatureError


class Postgres(Client):
    pass


class UserRepository(Protocol):
    async def get(self, user_id: int) -> str: ...


class Profiles(Client):
    def __init__(self, pg: Postgres, users: UserRepository) -> None:
        self.pg, self.users = pg, users


class Checkout(Client):
    def __init__(self, profiles: Profiles) -> None:
        self.profiles = profiles


class Orders(Client):
    def __init__(self, payments: "Payments") -> None:
        self.payments = payments


class Payments(Client):
    def __init__(self, orders: Orders) -> None:
        self.orders = orders


for root in (Checkout, Orders):
    try:
        Dependencies().resolve(root)
    except InvalidSignatureError as exc:
        print(f"{type(exc).__name__}: {exc}")

try:
    Dependencies().resolve(UserRepository)  # a type checker refuses this line, and so does the container
except InvalidSignatureError as exc:
    print(f"{type(exc).__name__}: {exc}")
```

```text
InvalidSignatureError: Argument "users" of "Profiles.__init__" is UserRepository, which is not a client (resolving Checkout -> Profiles)
CircularDependencyError: Circular dependency: Orders -> Payments -> Orders
InvalidSignatureError: UserRepository is not a client: subclass Client or NotSingletonClient
```

当 `__init__` 参数的类型提示是客户端时，它会被填入客户端。其他参数都必须有默认值，并且保持原样不动。
以下情况会以 `InvalidSignatureError` 失败：

| 没有默认值的 `__init__` 参数          | 消息                                              |
|---------------------------------------|---------------------------------------------------|
| 没有类型提示                          | `has no type hint`                                |
| 类型不是客户端                        | `is UserRepository, which is not a client`        |
| `Client \| None`                      | `is Postgres \| None, a client cannot be optional` |
| 仅限位置（`/`）的客户端参数           | `is positional-only, a client is passed by keyword` |

根本不是客户端的类，通过 `resolve()` 请求时，会在构建任何东西之前以 `UserRepository is not a client: subclass Client or NotSingletonClient` 失败。

相互循环依赖的客户端会以 `CircularDependencyError`（`InvalidSignatureError` 的子类）失败；无法求值的类型提示，
例如在函数内部定义的类或在 `TYPE_CHECKING` 下导入的类，则会引发一个说明此情况的 `InvalidSignatureError`。
如果错误来自 `inject()`，路径从函数开始：
`(resolving handler -> Checkout -> Profiles)`。在 [worker 或 job](workers-and-jobs.md) 中，
上述任一错误都会让这次运行在任何连接发生之前以退出码 `1` 失败。
