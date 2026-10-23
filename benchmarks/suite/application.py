"""
The services of the application of suite/infrastructure.py, up to its root, Api.
"""

from typing import Final

from suite.infrastructure import Kafka, Postgres, Redis, Sleeper

APPLICATION: Final = "application: 8 clients, connect() of 1–60 ms"  # noqa: RUF001
# The clients of the application, the N of its rows
APPLICATION_SIZE: Final = 8


class Repository(Sleeper):
    connect_seconds = 0.015
    disconnect_seconds = 0.001

    def __init__(self, postgres: Postgres) -> None:
        self.dependencies = (postgres,)


class Users(Sleeper):
    connect_seconds = 0.01
    disconnect_seconds = 0.001

    def __init__(self, repository: Repository, redis: Redis) -> None:
        self.dependencies = (repository, redis)


class Consumer(Sleeper):
    connect_seconds = 0.005
    disconnect_seconds = 0.005

    def __init__(self, kafka: Kafka) -> None:
        self.dependencies = (kafka,)


class Api(Sleeper):
    connect_seconds = 0.002
    disconnect_seconds = 0.001

    def __init__(self, users: Users, consumer: Consumer) -> None:
        self.dependencies = (users, consumer)
