"""Text, JSON, and HTML reports for SignalScope receiver data."""

from __future__ import annotations

from typing import Any

from django.template.loader import render_to_string
from django.db.models import Model

from signalscope.registry import ReceiverMap
from signalscope.risks import Risk
from signalscope.tracer import TraceNode, TraceResult


_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def build_report_data(
    receiver_map: ReceiverMap,
    trace_result: TraceResult | None = None,
    risks: list[Risk] | None = None,
) -> dict[str, Any]:
    """Convert model-keyed receiver data into a serialization-friendly report."""
    receiver_rows: list[dict[str, Any]] = []
    for sender, signals in receiver_map.items():
        for signal_name, receivers in signals.items():
            for receiver in receivers:
                receiver_rows.append(
                    {
                        "sender": _sender_label(sender),
                        "signal": signal_name,
                        "receiver": receiver.receiver,
                        "file": receiver.file,
                        "line": receiver.line,
                        "is_live": receiver.is_live,
                        "is_async": receiver.is_async,
                    }
                )

    trace_nodes = trace_result.flatten() if trace_result is not None else []
    trace_rows = [_trace_row(node) for node in trace_nodes]
    sorted_risks = sorted(
        risks or [],
        key=lambda risk: (
            _SEVERITY_ORDER.get(risk.severity, len(_SEVERITY_ORDER)),
            risk.location,
            risk.code,
        ),
    )
    return {
        "receivers": receiver_rows,
        "trace": trace_rows,
        "risks": [
            {
                "code": risk.code,
                "severity": risk.severity,
                "message": risk.message,
                "location": risk.location,
            }
            for risk in sorted_risks
        ],
    }


def format_text_report(
    receiver_map: ReceiverMap,
    trace_result: TraceResult | None = None,
    risks: list[Risk] | None = None,
) -> str:
    """Render a concise plain-text summary of the receiver map and trace."""
    data = build_report_data(receiver_map, trace_result, risks)
    lines = ["SignalScope receiver map"]
    for row in data["receivers"]:
        status = "live" if row["is_live"] else "static"
        location = _row_location(row["file"], row["line"])
        lines.append(
            f"- {row['sender']} | {row['signal']} | {row['receiver']} "
            f"[{status}] ({location})"
        )

    lines.append("")
    lines.append("Trace")
    if data["trace"]:
        for row in data["trace"]:
            lines.append(
                f"- depth {row['depth']}: {row['receiver']} "
                f"{row['duration_ms']:.1f} ms, {row['query_count']} queries"
            )
    else:
        lines.append("- No runtime trace.")

    lines.append("")
    lines.append("Risks")
    if data["risks"]:
        for risk in data["risks"]:
            lines.append(
                f"- {risk['severity']} {risk['code']}: {risk['message']} "
                f"({risk['location']})"
            )
    else:
        lines.append("- None detected.")
    return "\n".join(lines)


def render_html_report(
    receiver_map: ReceiverMap,
    trace_result: TraceResult | None = None,
    risks: list[Risk] | None = None,
) -> str:
    """Render a Django-template HTML report with a Mermaid trace graph."""
    data = build_report_data(receiver_map, trace_result, risks)
    context = {
        **data,
        "mermaid_graph": _mermaid_graph(trace_result),
    }
    return render_to_string("signalscope/report.html", context)


def _trace_row(node: TraceNode) -> dict[str, Any]:
    return {
        "signal": node.signal,
        "sender": node.sender,
        "instance": node.instance or "",
        "receiver": node.receiver,
        "depth": node.depth,
        "duration_ms": node.duration_ms,
        "query_count": node.query_count,
        "query_time_ms": node.query_time_ms,
        "file": node.file,
        "line": node.line,
    }


def _sender_label(sender: type[Model] | str | None) -> str:
    if sender is None:
        return "*"
    if isinstance(sender, str):
        return sender
    return str(sender._meta.label)


def _row_location(file: str | None, line: int | None) -> str:
    if file is None:
        return "unknown location"
    return f"{file}:{line}" if line is not None else file


def _mermaid_graph(trace_result: TraceResult | None) -> str:
    lines = ["graph TD"]
    if trace_result is None or not trace_result.roots:
        return "\n".join((*lines, '  empty["No traced receivers"]'))

    node_ids: dict[int, str] = {}
    pending: list[tuple[TraceNode, TraceNode | None]] = [
        (node, None) for node in reversed(trace_result.roots)
    ]
    next_id = 0
    while pending:
        node, parent = pending.pop()
        node_id = f"receiver{next_id}"
        next_id += 1
        node_ids[id(node)] = node_id
        label = _mermaid_escape(f"{node.signal}: {node.receiver} (depth {node.depth})")
        lines.append(f'  {node_id}["{label}"]')
        if parent is not None:
            lines.append(f"  {node_ids[id(parent)]} --> {node_id}")
        pending.extend((child, node) for child in reversed(node.children))
    return "\n".join(lines)


def _mermaid_escape(value: str) -> str:
    replacements = {
        "&": "#amp;",
        '"': "#quot;",
        "'": "#apos;",
        "<": "#lt;",
        ">": "#gt;",
        "[": "#91;",
        "]": "#93;",
        "\r": " ",
        "\n": " ",
    }
    return "".join(replacements.get(character, character) for character in value)
