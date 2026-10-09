# <a id="the-container"></a>El contenedor

[English](../../guide/container.md) · [Русский](../ru/container.md) · [简体中文](../zh-CN/container.md) · **Español** · [Português (Brasil)](../pt-BR/container.md) · [日本語](../ja/container.md) · [Polski](../pl/container.md)

← [Documentación](../README.es.md#documentation)

`Dependencies` es el contenedor. `DI` es una instancia global lista para usar; crea la tuya
cuando necesites aislamiento, por ejemplo en las pruebas.

| Método               | Descripción                                                             |
|----------------------|-------------------------------------------------------------------------|
| `resolve(cls)`       | Construye `cls` y su árbol de dependencias. Idempotente para `Client`.  |
| `inject(func)`       | Devuelve `functools.partial(func, ...)` con los argumentos de tipo cliente ya vinculados. Todo argumento de `func`, salvo `*args` / `**kwargs`, debe tener type hint. |
| `connect()`          | Llama a `connect()` en cada cliente resuelto, capa por capa.            |
| `disconnect()`       | Llama a `disconnect()` capa por capa en orden inverso y luego hace `flush()` del contenedor. |
| `async with`         | `connect()` al entrar, `disconnect()` al salir.                         |
| `mock(cls, new=None)`| Registra un Reemplazo para `cls` (por defecto, un mock con autospec) hasta el siguiente `flush()`. Debe llamarse antes de resolver `cls`. |
| `override(cls, new=None)` | Un Reemplazo que dura lo que dura un bloque `with`, seguido de `flush()`; consulta [Pruebas](testing.md). |
| `flush()`            | Olvida todos los clientes resueltos.                                    |
| `timings`            | Un `ClientTiming` por cliente del último `connect()`; ver [Tiempos de arranque](clients.md#startup-timings). |
| `graph()`            | Un `Graph` de los clientes resueltos con sus dependencias y capas, `to_mermaid()` incluido; ver [El grafo](clients.md#the-graph). |

El resultado de `inject()` conserva el tipo de retorno de la función, mientras que sus argumentos
restantes quedan sin tipar: un verificador de tipos no puede restar los argumentos cliente de una firma.

`resolve`, `inject`, `mock`, `override` y `flush` solo funcionan mientras el contenedor está
desconectado: todo el árbol se construye antes del arranque.

```python
async def main() -> None:
    deps = Dependencies()
    injected = deps.inject(handler)  # build the tree
    async with deps:  # connect
        await injected(42)
        deps.resolve(Cache)  # ConnectError: resolve(Cache): the container is already connected; ...
```

El contenedor puede resolver de forma segura desde varios hilos: un bloqueo por contenedor serializa `resolve`, `inject`,
`mock`, `override` y `flush`, así que un singleton pedido por dos hilos a la vez se construye una sola vez.
`connect()` y `disconnect()` pertenecen a un solo bucle de eventos.

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
