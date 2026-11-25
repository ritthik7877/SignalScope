from __future__ import annotations

import pytest

from demo_project.catalog.models import Product
from demo_project.orders.models import Order, OrderLine
from signalscope.risks import detect_risks
from signalscope.tracer import TraceNode, TraceResult, trace_signals


@pytest.mark.django_db
def test_detects_each_deliberate_demo_risk() -> None:
    products = [
        Product.objects.create(name=f"Product {index}", stock=10)
        for index in range(3)
    ]
    order = Order(product=products[0], quantity=1)
    Order.objects.bulk_create([order])
    OrderLine.objects.bulk_create(
        [
            OrderLine(order=order, product=product, quantity=1)
            for product in products
        ]
    )

    with trace_signals() as trace:
        order.save()

    risks = detect_risks(trace, max_queries=2)
    risk_codes = {risk.code for risk in risks}

    assert {"CIRCULAR", "HIDDEN_QUERIES", "SLOW", "DEEP_CHAIN", "ORPHAN"} <= risk_codes
    assert any(
        risk.code == "CIRCULAR" and "update_stock" in risk.message
        for risk in risks
    )
    assert any(
        risk.code == "HIDDEN_QUERIES"
        and "query_each_line" in risk.message
        and "repeated 3 times" in risk.message
        for risk in risks
    )
    assert any(
        risk.code == "SLOW" and "slow_order_handler" in risk.message
        for risk in risks
    )
    assert any(
        risk.code == "DEEP_CHAIN" and "step_four" in risk.message
        for risk in risks
    )
    orphan = next(risk for risk in risks if risk.code == "ORPHAN")
    assert "orphan_receiver" in orphan.message
    assert "workflows\\signals.py:" in orphan.location
    assert all(risk.location for risk in risks)
    assert [risk.severity for risk in risks] == sorted(
        (risk.severity for risk in risks),
        key={"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}.get,
    )


def test_query_risk_uses_strictly_greater_than_configured_limit() -> None:
    node = TraceNode(
        signal="post_save",
        sender="orders.Order",
        instance="orders.Order(pk=1)",
        receiver="demo.receiver",
        depth=1,
        start_time=0.0,
        query_count=5,
    )
    trace = TraceResult(roots=[node])

    assert not any(
        risk.code == "HIDDEN_QUERIES"
        for risk in detect_risks(trace, static_records=[], max_queries=5)
    )

    node.query_count = 6
    node.query_fingerprints["same-query"] = 6
    risks = detect_risks(trace, static_records=[], max_queries=5)

    assert any(
        risk.code == "HIDDEN_QUERIES" and "repeated 6 times" in risk.message
        for risk in risks
    )
