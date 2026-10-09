from nuke_di import Client

from graph.clients import Kafka
from graph.repositories import OrderRepository, UserRepository


class OrderService(Client):
    def __init__(self, orders: OrderRepository, users: UserRepository, kafka: Kafka) -> None:
        self.orders = orders
        self.users = users
        self.kafka = kafka

    async def create(self, user_id: int) -> int:
        order_id = await self.orders.create(await self.users.get(user_id))
        await self.kafka.send("orders.created", str(order_id))
        return order_id


class Notifications(Client):
    def __init__(self, users: UserRepository, kafka: Kafka) -> None:
        self.users = users
        self.kafka = kafka

    async def order_created(self, user_id: int, order_id: int) -> None:
        await self.kafka.send("notifications", f"order {order_id} for {await self.users.get(user_id)}")


class Checkout(Client):
    def __init__(self, orders: OrderService, notifications: Notifications) -> None:
        self.orders = orders
        self.notifications = notifications

    async def place(self, user_id: int) -> int:
        order_id = await self.orders.create(user_id)
        await self.notifications.order_created(user_id, order_id)
        return order_id
