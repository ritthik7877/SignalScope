"""Order signal handlers, including the stock-update cycle in the demo."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from demo_project.catalog.models import Product

from .models import Order


@receiver(post_save, sender=Order)
def update_stock(sender: type[Order], instance: Order, **kwargs: object) -> None:
    """Update stock, which saves the product and re-enters the order signal."""
    if instance.cycle_passes >= 1:
        return
    instance.cycle_passes += 1
    instance.save(update_fields=["cycle_passes"])
    product = Product.objects.get(pk=instance.product_id)
    product.stock = max(0, product.stock - instance.quantity)
    product.save(update_fields=["stock"])
