"""Zero-argument callable used by the SignalScope CLI example."""

from demo_project.orders.models import Order
from demo_project.workflows.signals import workflow_start


def trace_workflow() -> None:
    """Emit the demo's four-step workflow without requiring database setup."""
    workflow_start.send(sender=Order, instance=Order(pk=1))
