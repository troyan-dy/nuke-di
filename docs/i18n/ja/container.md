# <a id="the-container"></a>コンテナ

[English](../../guide/container.md) · [Русский](../ru/container.md) · [简体中文](../zh-CN/container.md) · [Español](../es/container.md) · [Português (Brasil)](../pt-BR/container.md) · **日本語** · [Polski](../pl/container.md)

← [ドキュメント](../README.ja.md#documentation)

`Dependencies` がコンテナです。`DI` はすぐに使えるグローバルインスタンスです。テストなどで分離が必要な場合は、独自のインスタンスを作成してください。

| メソッド             | 説明                                                                    |
|----------------------|-------------------------------------------------------------------------|
| `resolve(cls)`       | `cls` とその依存関係ツリーを構築します。`Client` に対しては冪等です。   |
| `inject(func)`       | クライアント引数を束縛した `functools.partial(func, ...)` を返します。`*args` / `**kwargs` を除く `func` のすべての引数に型ヒントが必要です。 |
| `connect()`          | 解決済みのすべてのクライアントに対して、レイヤーごとに `connect()` を呼び出します。 |
| `disconnect()`       | レイヤーの逆順に `disconnect()` を呼び出し、その後コンテナを `flush()` します。 |
| `async with`         | 開始時に `connect()`、終了時に `disconnect()` を呼び出します。          |
| `mock(cls, new=None)`| 次の `flush()` まで有効な `cls` の差し替え（デフォルトは autospec モック）を登録します。`cls` が解決される前に呼び出す必要があります。 |
| `override(cls, new=None)` | `with` ブロックの間だけ有効な差し替えを登録し、ブロックの終了後に `flush()` します。[テスト](testing.md)を参照してください。 |
| `flush()`            | 解決済みのクライアントをすべて破棄します。                              |
| `timings`            | 直近の `connect()` のクライアントごとの `ClientTiming`。[起動時間](clients.md#startup-timings)を参照。 |
| `graph()`            | 解決済みクライアントの `Graph`。依存とレイヤーを持ち、`to_mermaid()` 付き。[依存グラフ](clients.md#the-graph)を参照。 |

`inject()` の結果は関数の戻り値の型を保ちますが、残りの引数は型付けされません。型チェッカーはシグネチャからクライアント引数を差し引けないからです。

`resolve`、`inject`、`mock`、`override`、`flush` は、コンテナが切断されている間しか使えません。ツリー全体は起動前に構築されます。

```python
async def main() -> None:
    deps = Dependencies()
    injected = deps.inject(handler)  # build the tree
    async with deps:  # connect
        await injected(42)
        deps.resolve(Cache)  # ConnectError: resolve(Cache): the container is already connected; ...
```

コンテナは複数のスレッドから安全に解決できます。コンテナごとに一つのロックが `resolve`、`inject`、`mock`、`override`、`flush` を直列化するため、二つのスレッドが同時に求めたシングルトンは一度だけ構築されます。`connect()` と `disconnect()` は一つのイベントループに属します。

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
