# <a id="writing-an-integration"></a>Escrevendo uma integração

[English](../../guide/integrations.md) · [Русский](../ru/integrations.md) · [简体中文](../zh-CN/integrations.md) · [Español](../es/integrations.md) · **Português (Brasil)** · [日本語](../ja/integrations.md) · [Polski](../pl/integrations.md)

← [Documentação](../README.pt-BR.md#documentation)

Uma integração com um framework faz duas coisas: os handlers do framework recebem clientes pelo type hint, por meio
da injeção de dependências do próprio framework, e o container se conecta quando a aplicação inicia e se desconecta
quando ela para. As integrações de [FastAPI](fastapi.md), [Litestar](litestar.md), [FastStream](faststream.md) e
[aiogram](aiogram.md) são construídas sobre `nuke_di.integration`, e uma integração com outro framework não precisa
de nada do nuke-di além dele e da API pública.

| Nome | O que faz |
|---|---|
| `Framework(name, not_started, not_connected)` | O que é dito aos usuários de um framework quando falta um cliente; `{client}` em uma mensagem é o nome da classe do cliente. |
| `DependsFramework(..., depends, make_depends, per_container=True)` | Um framework que injeta por meio de marcadores `Depends(...)`: `depends` é a classe dos marcadores dele, `make_depends` constrói um para uma função. |
| `bind(call, container, framework)` | Reescreve a assinatura de um handler, de uma função de dependência ou de uma classe de dependência, e das dependências que ele usa: cada argumento de cliente vira `Annotated[Client, Depends(...)]`. Retorna o `Binding` de cada cliente. |
| `Binding` | Um argumento de cliente; `get()` retorna o cliente resolvido na inicialização, ou lança `not_started` / `not_connected`. |
| `running(container, bindings)` | Um gerenciador de contexto assíncrono: resolve os clientes de `bindings`, conecta o container e, na saída, aciona `Shutdown`, para as `BackgroundTasks` e desconecta. Um `ConnectError` ou `InitializeDependencyError` vira um `RuntimeError`, que um servidor reporta como falha na inicialização; um erro da árvore de clientes, como um ciclo, passa como está. |
| `wrap_lifespan(original, container, bindings)` | Um lifespan que roda o lifespan `original` da própria aplicação dentro de `running()`; `bindings` é chamado na inicialização, então os handlers declarados depois de `setup()` são encontrados. |
| `client_of(hint, *markers)` | O cliente que um type hint pede, ou `None`: `Client`, ou `Annotated[Client, ...]` sem nenhum dos `markers`. |
| `unique(bindings)` | `bindings` sem repetições: uma mesma dependência costuma ser alcançável a partir de vários handlers. |

## <a id="a-framework-with-depends"></a>Um framework com `Depends`

O FastAPI e o FastStream (por meio do fast-depends) leem `inspect.signature()` de um handler e chamam a dependência
de cada marcador `Depends(...)`, assim como qualquer framework cujos marcadores funcionem do mesmo jeito. Para eles, `bind()` substitui `users: UserService` por
`users: Annotated[UserService, Depends(binding.get)]`, e o framework faz o resto. A integração com o FastStream,
escrita apenas com o kit público, é este módulo:

```python
# myapp/faststream_di.py
from typing import Any

from faststream import Depends, FastStream

from nuke_di import DI, Dependencies
from nuke_di.integration import Binding, DependsFramework, bind, unique, wrap_lifespan


def _noop() -> None: ...


FRAMEWORK = DependsFramework(
    name="FastStream",
    # The class of FastStream's markers, and the function that builds one
    depends=type(Depends(_noop)),
    make_depends=Depends,
    not_started="{client} was not started with the app: declare its subscriber before the app starts",
    not_connected="{client} is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`",
    # FastStream builds a subscriber on every start: a function is bound once, whatever the container
    per_container=False,
)


def setup(app: FastStream, container: Dependencies = DI) -> None:
    # Connect the container around the app's own lifespan, with the clients found on startup
    app.lifespan_context = wrap_lifespan(app.lifespan_context, container, lambda: _bindings(app, container))
    for broker in app.brokers:
        # Rewrite a subscriber's function whenever FastStream builds the subscriber
        config = broker.config.fd_config
        config.call_decorators = (*config.call_decorators, _Rewrite(container))


class _Rewrite:
    def __init__(self, container: Dependencies) -> None:
        self.container = container

    def __call__(self, call: Any) -> Any:
        bind(call, self.container, FRAMEWORK)
        return call


def _bindings(app: FastStream, container: Dependencies) -> list[Binding]:
    # The clients of every subscriber and of the dependencies it declares, each once
    bindings: list[Binding] = []
    for broker in app.brokers:
        for subscriber in broker.subscribers:
            for item in subscriber.calls:
                bindings += bind(item.handler._declared_call, container, FRAMEWORK)
                for depends in item.dependencies:
                    bindings += bind(depends.dependency, container, FRAMEWORK)
    return unique(bindings)
```

Três coisas são específicas de cada framework, e cada integração as encontra nos internals daquele framework:

- **Quando a assinatura é lida.** `bind()` precisa rodar antes que o framework leia a assinatura do handler.
  O FastAPI a lê quando uma rota é declarada, então o `nuke_di.fastapi` faz o bind na sua classe de rota; o
  FastStream a lê quando constrói um subscriber, a cada início, então o módulo acima faz o bind em um hook
  `call_decorators`.
- **Onde estão os handlers.** Na inicialização, `wrap_lifespan()` chama `bindings()` para saber quais clientes
  resolver: a integração percorre as rotas, os subscribers ou as tarefas da aplicação, faz o bind de cada um e
  retorna os `Binding`s. Um cliente que nada na aplicação alcança não é conectado.
- **Onde está o lifespan.** `wrap_lifespan()` substitui o lifespan da aplicação; o lifespan da própria aplicação
  roda dentro dele, então o código de inicialização dela pode usar os clientes.

O `nuke_di.faststream` de verdade acrescenta o que o exemplo deixa de fora: o FastStream 0.6, as dependências de
brokers e routers, e um hook de reescrita por broker quando várias aplicações o compartilham.

### <a id="per-container"></a>`per_container`

`bind()` reescreve uma função no próprio lugar e lembra o container ao qual ela foi ligada. Com `per_container=True`
(o padrão, FastAPI), uma função ligada a um container e declarada de novo para outro é ligada de novo: o FastAPI
lê a assinatura de uma rota uma única vez, quando a rota é declarada, então cada aplicação mantém os bindings que
capturou, e duas aplicações sobre dois containers podem servir a mesma função ao mesmo tempo.

Com `per_container=False` (FastStream), uma função é ligada uma única vez, qualquer que seja o container, e toda
aplicação que inicia resolve os mesmos `Binding`s. O FastStream constrói um subscriber a cada início, e, sob um
broker de teste, até antes de o lifespan da aplicação rodar, então a assinatura não pode mudar de uma aplicação
para a outra. O custo: duas aplicações que compartilham uma função handler rodam uma de cada vez, e a segunda
lança `RuntimeError: ... is filled for another app that is running`. Escolha `False` quando o framework puder
ler uma assinatura de novo depois que a primeira aplicação já iniciou.

## <a id="a-framework-without-depends"></a>Um framework sem `Depends`

O Litestar fornece dependências pelo nome, e o aiogram as passa pelo nome a partir de um middleware. Ali `bind()`
não se aplica: use `Framework` para as mensagens, `client_of()` para encontrar os argumentos de cliente de um
handler, um `Binding` por cliente cujo `get` o framework chama pelos próprios meios, e `running()` dentro do
lifespan da aplicação. O `nuke_di.litestar` é o exemplo completo: ele registra `Provide(binding.get)` sob o nome
do argumento. O `nuke_di.aiogram` é outro: um inner middleware do dispatcher coloca `binding.instance` nos
dados de um update, sob o nome de cada argumento de cliente do handler que casou com ele, e `running()` envolve a
inicialização e o desligamento do dispatcher.

## <a id="checking-an-integration"></a>Verificando uma integração

`nuke_di.integration.testing.check()` roda, contra a sua integração, o contrato que toda integração cumpre:

- um handler recebe um cliente pelo type hint, conectado enquanto a aplicação roda;
- uma dependência de um handler recebe um cliente pelo type hint (apenas com um `DependsFramework`);
- `override()` antes da inicialização substitui um cliente que o handler recebe por meio de outro cliente;
- um handler chamado sem o lifespan da aplicação lança o erro `not_connected` do framework;
- um `connect()` que falha faz a inicialização da aplicação falhar com um `RuntimeError` e deixa o container
  esvaziado.

Ele traz os próprios clientes e handlers. Você fornece o framework e duas funções: `make_app(container,
handler)` retorna uma aplicação nova, configurada com `container`, que serve `handler`, uma `async def` sem
argumentos além de clientes; `run(app, lifespan)` é um gerenciador de contexto assíncrono que roda a aplicação,
com o lifespan dela apenas quando `lifespan` é verdadeiro, e entrega um `send()` que chama o handler uma vez
por meio do framework e lança o que o handler lançou. Para o módulo acima:

```python
# tests/test_contract.py
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from faststream import FastStream, TestApp
from faststream.nats import NatsBroker, TestNatsBroker

from myapp.faststream_di import FRAMEWORK, setup
from nuke_di import Dependencies
from nuke_di.integration.testing import Send, check


def make_app(container: Dependencies, handler: Callable[..., Any]) -> FastStream:
    broker = NatsBroker()
    app = FastStream(broker)
    setup(app, container)
    broker.subscriber("check")(handler)
    return app


@asynccontextmanager
async def run(app: FastStream, lifespan: bool) -> AsyncIterator[Send]:
    # connect_only: FastStream would guess it from the mention of TestApp below
    async with TestNatsBroker(app.broker, connect_only=lifespan) as broker:
        if lifespan:
            async with TestApp(app):
                yield lambda: broker.publish(None, "check")
        else:
            yield lambda: broker.publish(None, "check")


async def test_contract() -> None:
    await check(FRAMEWORK, make_app, run)
```

```console
$ pytest -q tests/test_contract.py
.                                                                        [100%]
1 passed in 0.29s
```

`check()` é uma corrotina: rode-a sob o pytest-asyncio ou o anyio. Ela lança um `ExceptionGroup` com cada caso
que falhou, cada um com uma nota que nomeia o caso. Sem a linha de `wrap_lifespan()` em `setup()`, o container
nunca se conecta:

```console
$ pytest -q --tb=short tests/test_contract.py
F                                                                        [100%]
  | ExceptionGroup: the FastStream integration breaks the nuke-di contract (4 sub-exceptions)
    | RuntimeError: Greeter is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`
    | FastStream integration, case handler: a handler takes a client by type hint, connected for the time the app runs
    | RuntimeError: Greeter is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`
    | FastStream integration, case dependency: a dependency of a handler takes a client by type hint
    | RuntimeError: Greeter is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`
    | FastStream integration, case override: override() before startup replaces a client the handler gets through another client
    | AssertionError: expected a RuntimeError with 'nuke-di clients failed to start' about Broken, got RuntimeError('Broken is not connected: start the app with its lifespan, e.g. `async with TestApp(app)`')
    | FastStream integration, case failed_connect: a failed connect() fails the app's startup with a RuntimeError and leaves the container flushed
FAILED tests/test_contract.py::test_contract - ExceptionGroup: the FastStream...
1 failed in 0.17s
```

(Tracebacks encurtados.) Um framework que reporta o erro de um handler em vez de lançá-lo precisa de um `send()`
que o lance: o test client de um framework HTTP retorna um 500, então o `send()` verifica o status. O Litestar
coloca o erro na resposta apenas com `debug=True`.

`nuke_di._integration`, o módulo privado em que este kit ficava antes da 1.14.0, ainda pode ser importado, com um
`DeprecationWarning`, e é removido na 1.15.0.
