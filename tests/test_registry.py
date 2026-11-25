from __future__ import annotations

import weakref

from django.apps import apps
from django.dispatch import Signal

from demo_project.orders.models import Order
from signalscope.registry import (
    LiveReceiverRecord,
    build_receiver_map,
    discover_signal_objects,
    read_live_receivers,
)
from signalscope.scanner import ReceiverRecord


def test_reads_builtin_and_custom_live_receivers() -> None:
    receivers = read_live_receivers()
    receiver_map = build_receiver_map()

    assert any(
        receiver.signal == "post_save"
        and receiver.sender is Order
        and receiver.receiver.__name__ == "update_stock"
        for receiver in receivers
    )
    assert any(
        receiver.signal == "workflow_start"
        and receiver.receiver.__name__ == "step_one"
        for receiver in receivers
    )
    assert "post_save" in discover_signal_objects()
    assert "workflow_start" in discover_signal_objects()
    assert any(
        receiver.receiver == "step_one" and receiver.is_live
        for receiver in receiver_map[None]["workflow_start"]
    )


def test_resolves_live_weakrefs_and_both_receiver_tuple_shapes(monkeypatch) -> None:
    signal = Signal()

    def legacy_receiver(**kwargs: object) -> None:
        return None

    def async_receiver(**kwargs: object) -> None:
        return None

    def collected_receiver(**kwargs: object) -> None:
        return None

    dead_reference = weakref.ref(collected_receiver)
    del collected_receiver
    monkeypatch.setattr(
        signal,
        "receivers",
        [
            ((id(legacy_receiver), id(Order)), weakref.ref(legacy_receiver)),
            ((id(async_receiver), id(Order)), weakref.ref(async_receiver), True),
            ((id(dead_reference), id(Order)), dead_reference, False),
        ],
    )

    receivers = read_live_receivers({"custom": signal})

    assert [(item.receiver, item.sender, item.is_async) for item in receivers] == [
        (legacy_receiver, Order, False),
        (async_receiver, Order, True),
    ]


def test_merges_static_and_live_receivers_and_keeps_unresolved_sender() -> None:
    def receiver_fn(**kwargs: object) -> None:
        return None

    static = [
        ReceiverRecord(
            signal="post_save",
            sender="Order",
            receiver="receiver_fn",
            file="orders/signals.py",
            line=12,
        ),
        ReceiverRecord(
            signal="post_save",
            sender="missing_app.Ghost",
            receiver="orphan_receiver",
            file="orphan/signals.py",
            line=20,
        ),
    ]
    live = [LiveReceiverRecord("post_save", Order, receiver_fn, False)]

    receiver_map = build_receiver_map(static, live)

    merged = receiver_map[Order]["post_save"]
    assert len(merged) == 1
    assert merged[0].is_live is True
    assert merged[0].receiver == "receiver_fn"
    assert merged[0].file == "orders/signals.py"
    assert merged[0].line == 12
    assert receiver_map["missing_app.Ghost"]["post_save"][0].is_live is False
    assert apps.get_model("orders", "Order") is Order
