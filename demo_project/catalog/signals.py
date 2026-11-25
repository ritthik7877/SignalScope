"""Catalog signal handlers used by the demo."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from demo_project.orders.models import Order

from .models import Product


@receiver(post_save, sender=Product)
def save_related_order(sender: type[Product], instance: Product, **kwargs: object) -> None:
    """Save an order again when product stock changes, forming a bounded cycle."""
    order = Order.objects.filter(product=instance).first()
    if order is not None and not getattr(order, "_cycle_reentered", False):
        order._cycle_reentered = True
        order.save()
