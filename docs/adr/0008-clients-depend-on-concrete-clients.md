# Clients depend on concrete clients: no interface binding

An argument of `__init__` is filled when its type hint is a client class, and the hint is the only registration there is. Issue #9 proposed `DI.bind(UserRepository, PostgresUsers)`, so that a client could depend on a `Protocol` or an ABC and the container would build the bound implementation. It was rejected on 2026-10-08 as overcomplicating the framework. Binding adds a second registry next to the type hints, with its own rules: bind before resolve, what binding one abstraction twice means, how `bind` and `override()` interact. It also opens the way to qualifiers and multibinding, which every library that has binding ends up adding. What #9 wanted is reachable without it. A test swaps a concrete client with `mock()` / `override()`, and the Replacement can be any object, an in-memory fake included. A choice per environment is made by a client that picks the implementation from its settings in `connect()` and delegates to it. An argument typed with a `Protocol` keeps failing with `InvalidSignatureError` (`which is not a client`), and the mypy plugin reports it before the process runs.

## Considered Options

- **`DI.bind(abstract, concrete)`** (#9): singleton semantics follow the concrete class, binding only while disconnected, an unbound `Protocol` named in the error. Prior art: wireup `as_type`, dishka `alias`, injector `binder.bind`, punq `register`.
- **Qualifiers** (`Annotated[Postgres, "replica"]`) to tell two instances of one class apart: two clients are two classes, `class Replica(Postgres)` with its own settings.
- **Multibinding** (`list[Plugin]`) to inject every implementation of an abstraction: a client that takes the plugins it aggregates as arguments of its `__init__`.
