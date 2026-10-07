# An entrypoint runs at decoration time

`@job` and `@worker` start the Run from inside the decorator when the decorated function's module is `__main__`, so `python -m app.jobs.sync` is the whole contract and an entrypoint file needs no `if __name__ == "__main__":` tail. When the module is imported normally, the decorator returns the function unchanged, so tests call it directly with mocks.

The cost is that the module stops executing at the decorator: code below the entrypoint never runs. The rule is one entrypoint per module, defined last.

## Considered Options

- **An explicit tail** (`if __name__ == "__main__": sync.run()`): no surprise, but every entrypoint file carries the same boilerplate line and forgetting it gives a module that silently does nothing.
- **A generic CLI** (`nuke-di run app.jobs.sync:sync`): no boilerplate either, but a second way to start the same thing and a command-line surface to maintain.
- **Deferring the Run to `atexit`**, so the whole module executes first: exit codes would have to go through `os._exit()`, which skips other exit handlers and buffer flushing, and threads are already shut down by then. That trades one visible rule for several hidden ones.
