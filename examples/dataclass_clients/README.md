# Dataclass clients

A service layer whose classes are `@client_dataclass(frozen=True)`: the fields are the injected
dependencies, so a service with four of them needs no `__init__` that only copies its arguments
to `self`. Useful once services have more dependencies than methods.

| File               | What it holds                                                                    |
|--------------------|----------------------------------------------------------------------------------|
| `clients.py`       | The infrastructure clients: `Postgres`, `Redis`, `PaymentGateway`, `Mailer`     |
| `services.py`      | `Payments` and `Checkout`, frozen dataclass clients                              |
| `main.py`          | `handler`, which places an order through `Checkout`                              |
| `test_services.py` | `Checkout` built directly with mocks, and the same class resolved by a container |

## Run

```console
$ cd examples
$ uv run python -m dataclass_clients.main
Checkout(pg=<dataclass_clients.clients.Postgres object at 0x103045fd0>, cache=<dataclass_clients.clients.Redis object at 0x103046120>, payments=Payments(pg=<dataclass_clients.clients.Postgres object at 0x103045fd0>, gateway=<dataclass_clients.clients.PaymentGateway object at 0x103046270>), mailer=<dataclass_clients.clients.Mailer object at 0x103046510>)
postgres: connected
redis: connected
postgres: order 1 of alice for 120
postgres: order 1 paid by ch_120
redis: deleted cart:alice
mailer: to alice: order 1 is paid
placed order 1
postgres: disconnected
redis: disconnected
```

The addresses differ from run to run; the same `Postgres` address appears in `Checkout` and in
`Payments`.

## Test

```console
$ uv run pytest -q dataclass_clients
..                                                                       [100%]
2 passed in 0.04s
```

## What to look at

- `class Checkout(Client)` subclasses `Client` as well as being decorated: the decorator is typed as
  an identity, so the base class is what tells mypy and pyright that `Checkout` is a client.
- The generated `__repr__` prints the whole tree below a service, which helps when you debug wiring.
- `test_place_order_without_a_container` calls the generated `__init__` with mocks: no container,
  nothing connects. Wrong field names or a missing field are type errors.
- `frozen=True` keeps a service from swapping a dependency after it is built. Infrastructure clients
  that keep state, like the counter of `Postgres`, stay plain classes.
