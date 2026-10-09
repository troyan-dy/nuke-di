# <a id="the-container"></a>O container

[English](../../guide/container.md) · [Русский](../ru/container.md) · [简体中文](../zh-CN/container.md) · [Español](../es/container.md) · **Português (Brasil)** · [日本語](../ja/container.md) · [Polski](../pl/container.md)

← [Documentação](../README.pt-BR.md#documentation)

`Dependencies` é o container. `DI` é uma instância global pronta para uso; crie a sua
quando precisar de isolamento, por exemplo nos testes.

| Método               | Descrição                                                               |
|----------------------|-------------------------------------------------------------------------|
| `resolve(cls)`       | Constrói `cls` e a sua árvore de dependências. Idempotente para `Client`. |
| `inject(func)`       | Retorna `functools.partial(func, ...)` com os argumentos de cliente já vinculados. Todo argumento de `func`, exceto `*args` / `**kwargs`, precisa ter type hint. |
| `connect()`          | Chama `connect()` em cada cliente resolvido, cada um depois das suas dependências. |
| `disconnect()`       | Chama `disconnect()` em cada cliente, cada um depois dos seus consumidores, e depois faz `flush()` do container. |
| `async with`         | `connect()` na entrada, `disconnect()` na saída.                        |
| `mock(cls, new=None)`| Registra um substituto para `cls` (por padrão, um mock com autospec) até o próximo `flush()`. Precisa vir antes de `cls` ser resolvido. |
| `override(cls, new=None)` | Um substituto que vale durante um bloco `with`, seguido de `flush()`; veja [Testes](testing.md). |
| `flush()`            | Esquece todos os clientes resolvidos.                                   |
| `timings`            | Um `ClientTiming` por cliente do último `connect()`; veja [Tempos de inicialização](clients.md#startup-timings). |
| `graph()`            | Um `Graph` dos clientes resolvidos com suas dependências, `to_mermaid()` incluído; veja [O grafo](clients.md#the-graph). |

O resultado de `inject()` mantém o tipo de retorno da função, enquanto seus argumentos restantes
ficam sem tipo: um verificador de tipos não consegue subtrair os argumentos cliente de uma assinatura.

`resolve`, `inject`, `mock`, `override` e `flush` só funcionam enquanto o container está desconectado:
a árvore inteira é construída antes da inicialização.

```python
async def main() -> None:
    deps = Dependencies()
    injected = deps.inject(handler)  # build the tree
    async with deps:  # connect
        await injected(42)
        deps.resolve(Cache)  # ConnectError: resolve(Cache): the container is already connected; ...
```

O container pode resolver com segurança a partir de várias threads: um lock por container serializa `resolve`, `inject`,
`mock`, `override` e `flush`, então um singleton pedido por duas threads ao mesmo tempo é construído uma
única vez. `connect()` e `disconnect()` pertencem a um único event loop.

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
