# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- `inject()` now raises `InvalidSignatureError` for a function argument without a type hint.
  The check never fired before, so such functions were accepted silently.
  Unannotated `*args` / `**kwargs` are still allowed.

### Added

- Line and branch coverage is 100% and enforced in CI (`fail_under = 100`).

## [1.0.0] - 2026-10-08

First public release, extracted from the `nuke.di` package of the nuke framework.

### Added

- `Client` / `NotSingletonClient` base classes with async `connect()` / `disconnect()` hooks.
- `Dependencies` container and the global `DI` instance: `resolve`, `inject`, `mock`, `flush`,
  `connect` / `disconnect` and `async with` support.
- `client_dataclass` decorator.
- `CONNECT_TIMEOUT_SECONDS` setting and `DependenciesSettings`.

### Changed compared to `nuke.di`

- No runtime dependencies: settings no longer use `pydantic-settings`, logging uses the standard
  `logging` module under the `nuke_di` logger.
- Clients no longer get a per-class `_logger` attribute.

[Unreleased]: https://github.com/troyan-dy/nuke-di/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/troyan-dy/nuke-di/releases/tag/v1.0.0
