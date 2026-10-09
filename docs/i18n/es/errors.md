# <a id="errors"></a>Errores

[English](../../guide/errors.md) · [Русский](../ru/errors.md) · [简体中文](../zh-CN/errors.md) · **Español** · [Português (Brasil)](../pt-BR/errors.md) · [日本語](../ja/errors.md) · [Polski](../pl/errors.md)

← [Documentación](../README.es.md#documentation)

| Excepción                   | Se lanza cuando                                           |
|-----------------------------|-----------------------------------------------------------|
| `InitializeDependencyError` | El `__init__` de un cliente lanzó una excepción           |
| `ConnectError`              | El `connect()` de un cliente lanzó una excepción, o el estado del contenedor no es el correcto (por ejemplo, resolver después de conectar, mockear un cliente que ya está resuelto o hacer override de un contenedor que tiene clientes resueltos) |
| `ConnectTimeoutError`       | El `connect()` de un cliente superó `CONNECT_TIMEOUT_SECONDS` |
| `InvalidSignatureError`     | El `__init__` de un cliente tiene un argumento obligatorio que no es un cliente, `inject()` recibió una función con un argumento sin type hint, `resolve()` recibió una clase que no es un cliente, o un parámetro de un punto de entrada tiene un tipo no admitido o un flag en conflicto; consulta [Cuando el árbol no se puede construir](clients.md#when-the-tree-cannot-be-built) |
| `CircularDependencyError`   | Hay clientes que dependen unos de otros en un ciclo; es una subclase de `InvalidSignatureError` |
| `UsageError`                | La línea de comandos de un worker o un job no coincide con sus parámetros; se guarda como `Run.error`, código de salida `2` |

`InitializeDependencyError` y `ConnectError` derivan de `SystemExit`: se espera que una aplicación
cuyas dependencias no pueden arrancar se detenga. Captúralas de forma explícita si necesitas
otro comportamiento; la excepción original está disponible en `__cause__`.

`nuke-di` escribe sus logs con el módulo estándar `logging`, en el logger `nuke_di`, con
[campos estructurados](workers-and-jobs.md#startup-metrics-and-structured-logs) para los pipelines de logs.
