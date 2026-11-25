"""Deliberately slow, query-heavy, deep, and orphaned signal wiring."""

import time

from django.db.models.signals import post_save
from django.dispatch import Signal, receiver

from demo_project.catalog.models import Product
from demo_project.orders.models import Order

workflow_start = Signal()
workflow_step_one = Signal()
workflow_step_two = Signal()
workflow_step_three = Signal()
workflow_step_four = Signal()


@receiver(post_save, sender=Order)
def query_each_line(sender: type[Order], instance: Order, **kwargs: object) -> None:
    """Demonstrate an N+1 query pattern over an order's lines."""
    for line in instance.lines.all():
        Product.objects.get(pk=line.product_id)


@receiver(post_save, sender=Order)
def slow_order_handler(sender: type[Order], instance: Order, **kwargs: object) -> None:
    """Demonstrate a slow receiver."""
    time.sleep(0.2)


def start_workflow(sender: type[Order], instance: Order, **kwargs: object) -> None:
    workflow_start.send(sender=sender, instance=instance)


def step_one(sender: type[Order], instance: Order, **kwargs: object) -> None:
    workflow_step_one.send(sender=sender, instance=instance)


def step_two(sender: type[Order], instance: Order, **kwargs: object) -> None:
    workflow_step_two.send(sender=sender, instance=instance)


def step_three(sender: type[Order], instance: Order, **kwargs: object) -> None:
    workflow_step_three.send(sender=sender, instance=instance)


def step_four(sender: type[Order], instance: Order, **kwargs: object) -> None:
    return None


workflow_start.connect(step_one)
workflow_step_one.connect(step_two)
workflow_step_two.connect(step_three)
workflow_step_three.connect(step_four)
post_save.connect(start_workflow, sender=Order)


def orphan_receiver(sender: object, **kwargs: object) -> None:
    return None


post_save.connect(orphan_receiver, sender="missing_app.Ghost")
