# <a id="the-container"></a>Kontener

[English](../../guide/container.md) · [Русский](../ru/container.md) · [简体中文](../zh-CN/container.md) · [Español](../es/container.md) · [Português (Brasil)](../pt-BR/container.md) · [日本語](../ja/container.md) · **Polski**

← [Dokumentacja](../README.pl.md#documentation)

`Dependencies` to kontener. `DI` to gotowa do użycia instancja globalna; utwórz własną,
gdy potrzebujesz izolacji, np. w testach.

| Metoda               | Opis                                                                    |
|----------------------|-------------------------------------------------------------------------|
| `resolve(cls)`       | Buduje `cls` i jego drzewo zależności. Idempotentne dla `Client`.       |
| `inject(func)`       | Zwraca `functools.partial(func, ...)` z podpiętymi argumentami-klientami. Każdy argument `func` poza `*args` / `**kwargs` musi mieć adnotację typu. |
| `connect()`          | Wywołuje `connect()` na każdym rozwiązanym kliencie, każdym po jego zależnościach. |
| `disconnect()`       | Wywołuje `disconnect()` na każdym kliencie, każdym po jego konsumentach, a potem `flush()` na kontenerze. |
| `async with`         | `connect()` przy wejściu, `disconnect()` przy wyjściu.                  |
| `mock(cls, new=None)`| Rejestruje zamiennik dla `cls` (domyślnie mock z autospec) do następnego `flush()`. Trzeba wywołać przed rozwiązaniem `cls`. |
| `override(cls, new=None)` | Zamiennik na czas bloku `with`, a potem `flush()`; zob. [Testowanie](testing.md). |
| `flush()`            | Zapomina wszystkich rozwiązanych klientów.                              |
| `timings`            | Po jednym `ClientTiming` na klienta ostatniego `connect()`; zob. [Czasy startu](clients.md#startup-timings). |
| `graph()`            | `Graph` rozwiązanych klientów z ich zależnościami, wraz z `to_mermaid()`; zob. [Graf](clients.md#the-graph). |

Wynik `inject()` zachowuje typ zwracany funkcji, a jej pozostałe argumenty pozostają bez typów:
sprawdzacz typów nie potrafi odjąć argumentów-klientów od sygnatury.

`resolve`, `inject`, `mock`, `override` i `flush` działają tylko wtedy, gdy kontener jest rozłączony:
całe drzewo buduje się przed startem.

```python
async def main() -> None:
    deps = Dependencies()
    injected = deps.inject(handler)  # build the tree
    async with deps:  # connect
        await injected(42)
        deps.resolve(Cache)  # ConnectError: resolve(Cache): the container is already connected; ...
```

Kontener można bezpiecznie używać z kilku wątków: jedna blokada na kontener serializuje `resolve`, `inject`,
`mock`, `override` i `flush`, więc singleton zażądany przez dwa wątki naraz jest budowany raz.
`connect()` i `disconnect()` należą do jednej pętli zdarzeń.

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
