# <a id="errors"></a>Błędy

[English](../../guide/errors.md) · [Русский](../ru/errors.md) · [简体中文](../zh-CN/errors.md) · [Español](../es/errors.md) · [Português (Brasil)](../pt-BR/errors.md) · [日本語](../ja/errors.md) · **Polski**

← [Dokumentacja](../README.pl.md#documentation)

| Wyjątek                     | Zgłaszany, gdy                                            |
|-----------------------------|-----------------------------------------------------------|
| `InitializeDependencyError` | `__init__` klienta zgłosił wyjątek                        |
| `ConnectError`              | `connect()` klienta zgłosił wyjątek albo kontener jest w niewłaściwym stanie (np. rozwiązywanie po połączeniu, mockowanie już rozwiązanego klienta, `override()` na kontenerze z rozwiązanymi klientami) |
| `ConnectTimeoutError`       | `connect()` klienta przekroczył `CONNECT_TIMEOUT_SECONDS` |
| `InvalidSignatureError`     | `__init__` klienta ma wymagany argument, który nie jest klientem, `inject()` dostał funkcję z argumentem bez adnotacji typu, `resolve()` dostał klasę, która nie jest klientem, albo parametr punktu wejścia ma nieobsługiwany typ lub flagę, która z czymś koliduje; zob. [Gdy nie da się zbudować drzewa](clients.md#when-the-tree-cannot-be-built) |
| `CircularDependencyError`   | Klienci zależą od siebie nawzajem w cyklu; podklasa `InvalidSignatureError` |
| `UsageError`                | Wiersz poleceń workera lub joba nie pasuje do jego parametrów; zapisywany jako `Run.error`, kod wyjścia `2` |

`InitializeDependencyError` i `ConnectError` dziedziczą po `SystemExit`: aplikacja, której
zależności nie mogą wystartować, powinna się zatrzymać. Przechwyć je jawnie, jeśli potrzebujesz
innego zachowania; pierwotny wyjątek jest dostępny jako `__cause__`.

`nuke-di` loguje przez standardowy moduł `logging` w loggerze `nuke_di`, z
[polami strukturalnymi](workers-and-jobs.md#startup-metrics-and-structured-logs) dla potoków logów.
