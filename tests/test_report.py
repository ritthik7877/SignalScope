from __future__ import annotations

from signalscope.registry import ReceiverInfo
from signalscope.report import render_html_report
from signalscope.risks import Risk
from signalscope.tracer import TraceNode, TraceResult


def test_html_report_contains_map_graph_timings_and_escapes_untrusted_names() -> None:
    unsafe_name = '<script>alert("x")</script>'
    receiver_map = {
        "<img src=x onerror=alert(1)>": {
            "post_save": [
                ReceiverInfo(
                    signal="post_save",
                    sender="<img src=x onerror=alert(1)>",
                    receiver=unsafe_name,
                    file="unsafe.py",
                    line=9,
                    is_live=True,
                    is_async=False,
                )
            ]
        }
    }
    trace = TraceResult(
        roots=[
            TraceNode(
                signal="post_save",
                sender="orders.Order",
                instance="orders.Order(pk=3)",
                receiver=unsafe_name,
                depth=1,
                start_time=1.0,
                duration_ms=2.0,
                query_count=1,
                query_time_ms=0.5,
                file="unsafe.py",
                line=9,
            )
        ]
    )
    risks = [Risk("SLOW", "MEDIUM", unsafe_name, "unsafe.py:9")]

    html = render_html_report(receiver_map, trace, risks)

    assert "<table>" in html
    assert "mermaid" in html
    assert "Duration (ms)" in html
    assert "&lt;script&gt;" in html
    assert "#lt;script#gt;" in html
    assert "<script>alert" not in html
    assert "<img src=x onerror=alert(1)>" not in html
