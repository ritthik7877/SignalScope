from django.apps import AppConfig


class OrdersConfig(AppConfig):
    name = "demo_project.orders"
    label = "orders"

    def ready(self) -> None:
        from . import signals  # noqa: F401
