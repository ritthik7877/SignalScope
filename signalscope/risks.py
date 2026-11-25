"""Risk detection for traced receivers and statically connected senders."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from django.apps import apps
from django.conf import settings

from signalscope.scanner import ReceiverRecord, scan_installed_apps
from signalscope.tracer import TraceNode, TraceResult


_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


@dataclass(frozen=True)
class Risk:
    """A detected signal risk and its source location."""

    code: str
    severity: str
    message: str
    location: str


def detect_risks(
    trace_result: TraceResult | None = None,
    *,
    static_records: Iterable[ReceiverRecord] | None = None,
    max_queries: int | None = None,
    slow_ms: float | None = None,
    max_depth: int | None = None,
) -> list[Risk]:
    """Detect circular, query-heavy, slow, deep, and orphaned signal receivers."""
    config = getattr(settings, "SIGNALSCOPE", {})
    query_limit = _configured_int(max_queries, config, "MAX_QUERIES", 5)
    slow_limit = _configured_float(slow_ms, config, "SLOW_MS", 100.0)
    depth_limit = _configured_int(max_depth, config, "MAX_DEPTH", 3)
    records = list(scan_installed_apps() if static_records is None else static_records)
    risks: list[Risk] = []

    if trace_result is not None:
        _collect_trace_risks(trace_result.roots, query_limit, slow_limit, depth_limit, risks)

    for record in records:
        if record.sender is not None and not _sender_is_installed_model(record.sender):
            risks.append(
                Risk(
                    code="ORPHAN",
                    severity="HIGH",
                    message=f"{record.receiver} is connected to unknown sender model {record.sender}.",
                    location=_source_location(record.file, record.line, record.receiver),
                )
            )

    return sorted(
        risks,
        key=lambda risk: (
            _SEVERITY_ORDER.get(risk.severity, len(_SEVERITY_ORDER)),
            risk.location,
            risk.code,
            risk.message,
        ),
    )


def _collect_trace_risks(
    roots: list[TraceNode],
    max_queries: int,
    slow_ms: float,
    max_depth: int,
    risks: list[Risk],
) -> None:
    pending: list[tuple[TraceNode, tuple[TraceNode, ...]]] = [
        (node, ()) for node in reversed(roots)
    ]
    while pending:
        node, ancestors = pending.pop()
        repeated_receiver = any(ancestor.receiver == node.receiver for ancestor in ancestors)
        repeated_signal = any(
            ancestor.signal == node.signal
            and ancestor.sender == node.sender
            and ancestor.instance == node.instance
            for ancestor in ancestors
        )
        if repeated_receiver or repeated_signal:
            reason = "receiver re-entered" if repeated_receiver else "signal, sender, and instance repeated"
            risks.append(
                _trace_risk(
                    "CIRCULAR",
                    "CRITICAL",
                    f"Circular signal chain detected: {reason} at {node.receiver}.",
                    node,
                )
            )

        if node.query_count > max_queries:
            repeated_queries = max(node.query_fingerprints.values(), default=0)
            detail = (
                f"; one SQL pattern repeated {repeated_queries} times"
                if repeated_queries > 1
                else ""
            )
            risks.append(
                _trace_risk(
                    "HIDDEN_QUERIES",
                    "HIGH",
                    f"{node.receiver} executed {node.query_count} queries "
                    f"(limit {max_queries}){detail}.",
                    node,
                )
            )
        if node.duration_ms > slow_ms:
            risks.append(
                _trace_risk(
                    "SLOW",
                    "MEDIUM",
                    f"{node.receiver} took {node.duration_ms:.1f} ms "
                    f"(limit {slow_ms:.1f} ms).",
                    node,
                )
            )
        if node.depth > max_depth:
            risks.append(
                _trace_risk(
                    "DEEP_CHAIN",
                    "MEDIUM",
                    f"{node.receiver} reached depth {node.depth} (limit {max_depth}).",
                    node,
                )
            )
        pending.extend((child, (*ancestors, node)) for child in reversed(node.children))


def _trace_risk(code: str, severity: str, message: str, node: TraceNode) -> Risk:
    return Risk(
        code=code,
        severity=severity,
        message=message,
        location=_source_location(node.file, node.line, node.receiver),
    )


def _sender_is_installed_model(sender: str) -> bool:
    normalized = sender.casefold()
    short_name = sender.rsplit(".", 1)[-1].casefold()
    for model in apps.get_models():
        if normalized in {
            model._meta.label.casefold(),
            model._meta.label_lower.casefold(),
        } or short_name == model.__name__.casefold():
            return True
    return False


def _source_location(file: str | None, line: int | None, fallback: str) -> str:
    if file is None:
        return fallback
    return f"{file}:{line}" if line is not None else file


def _configured_int(
    explicit: int | None,
    config: object,
    key: str,
    default: int,
) -> int:
    if explicit is not None:
        return explicit
    if isinstance(config, Mapping):
        value = config.get(key)
        if isinstance(value, int):
            return value
    return default


def _configured_float(
    explicit: float | None,
    config: object,
    key: str,
    default: float,
) -> float:
    if explicit is not None:
        return explicit
    if isinstance(config, Mapping):
        value = config.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return default
