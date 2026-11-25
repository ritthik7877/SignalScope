from __future__ import annotations

import time

import pytest
from django.db import connection
from django.dispatch import Signal

from demo_project.orders.models import Order
from signalscope.tracer import TraceNode, trace_signals


def _only_child(node: TraceNode) -> TraceNode:
    assert len(node.children) == 1
    return node.children[0]


@pytest.mark.django_db
def test_traces_nested_receivers_with_timing_queries_and_original_responses() -> None:
    outer_signal = Signal()
    inner_signal = Signal()

    def inner_receiver(sender: object, **kwargs: object) -> str:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        time.sleep(0.01)
        return "inner response"

    def outer_receiver(sender: object, **kwargs: object) -> str:
        inner_signal.send(sender=sender, instance=kwargs["instance"])
        return "outer response"

    outer_signal.connect(outer_receiver, weak=False)
    inner_signal.connect(inner_receiver, weak=False)
    instance = Order(pk=42)

    with trace_signals() as result:
        responses = outer_signal.send(sender=Order, instance=instance)

    assert responses == [(outer_receiver, "outer response")]
    assert responses[0][0] is outer_receiver
    assert len(result.roots) == 1
    root = result.roots[0]
    child = _only_child(root)
    assert root.depth == 1
    assert child.depth == 2
    assert child.receiver.endswith(".inner_receiver")
    assert child.instance == "orders.Order(pk=42)"
    assert child.end_time is not None
    assert child.duration_ms >= 8
    assert child.query_count == 1
    assert child.query_time_ms >= 0
    assert result.flatten() == [root, child]


def test_restores_signal_methods_after_exception_and_preserves_robust_results() -> None:
    signal = Signal()

    def failing_receiver(sender: object, **kwargs: object) -> None:
        raise ValueError("receiver failure")

    signal.connect(failing_receiver, weak=False)
    original_send = Signal.send
    original_send_robust = Signal.send_robust
    original_live_receivers = Signal._live_receivers

    with trace_signals() as result:
        responses = signal.send_robust(sender=Order)

    assert len(responses) == 1
    assert responses[0][0] is failing_receiver
    assert isinstance(responses[0][1], ValueError)
    assert str(responses[0][1]) == "receiver failure"
    assert result.flatten()[0].receiver.endswith(".failing_receiver")
    assert Signal.send is original_send
    assert Signal.send_robust is original_send_robust
    assert Signal._live_receivers is original_live_receivers


def test_context_manager_restores_methods_when_receiver_raises() -> None:
    signal = Signal()
    error = RuntimeError("expected")

    def receiver(sender: object, **kwargs: object) -> None:
        raise error

    signal.connect(receiver, weak=False)
    original_send = Signal.send

    with pytest.raises(RuntimeError, match="expected") as caught:
        with trace_signals():
            signal.send(sender=Order)

    assert caught.value is error
    assert Signal.send is original_send


def test_trace_context_is_usable_as_a_decorator() -> None:
    signal = Signal()

    def receiver(sender: object, **kwargs: object) -> str:
        return "decorated"

    signal.connect(receiver, weak=False)

    @trace_signals()
    def emit_signal() -> str:
        return signal.send(sender=Order)[0][1]

    assert emit_signal() == "decorated"


def test_traces_async_receivers_without_changing_send_responses() -> None:
    signal = Signal()

    async def async_receiver(sender: object, **kwargs: object) -> str:
        return "async response"

    signal.connect(async_receiver, weak=False)

    with trace_signals() as result:
        responses = signal.send(sender=Order)

    assert responses == [(async_receiver, "async response")]
    assert responses[0][0] is async_receiver
    assert result.flatten()[0].receiver.endswith(".async_receiver")
    assert result.flatten()[0].depth == 1
