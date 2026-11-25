"""Runtime tracing for Django signal receiver calls."""

from __future__ import annotations

import hashlib
import inspect
import threading
import time
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from functools import wraps
from typing import Any

from asgiref.sync import iscoroutinefunction
from django.db import connections
from django.dispatch import Signal

from signalscope.registry import discover_signal_objects


@dataclass
class TraceNode:
    """Timing and query measurements for one signal receiver invocation."""

    signal: str
    sender: str
    instance: str | None
    receiver: str
    depth: int
    start_time: float
    end_time: float | None = None
    duration_ms: float = 0.0
    query_count: int = 0
    query_time_ms: float = 0.0
    query_fingerprints: dict[str, int] = field(default_factory=dict)
    file: str | None = None
    line: int | None = None
    children: list["TraceNode"] = field(default_factory=list)


@dataclass
class TraceResult:
    """The root receiver calls captured during one tracing context."""

    roots: list[TraceNode] = field(default_factory=list)

    def flatten(self) -> list[TraceNode]:
        """Return all receiver nodes in depth-first order."""
        nodes: list[TraceNode] = []
        pending = list(reversed(self.roots))
        while pending:
            node = pending.pop()
            nodes.append(node)
            pending.extend(reversed(node.children))
        return nodes


_active_result: ContextVar[TraceResult | None] = ContextVar("signalscope_result", default=None)
_active_stack: ContextVar[tuple[TraceNode, ...]] = ContextVar("signalscope_stack", default=())
_patch_lock = threading.RLock()
_patch_depth = 0
_original_send: Callable[..., Any] | None = None
_original_send_robust: Callable[..., Any] | None = None
_original_live_receivers: Callable[..., Any] | None = None
_signal_names: dict[int, str] = {}


@contextmanager
def trace_signals() -> Generator[TraceResult, None, None]:
    """Trace receiver calls inside a context, or decorate a callable with ``@trace_signals()``."""
    result = TraceResult()
    result_token = _active_result.set(result)
    stack_token = _active_stack.set(())
    try:
        _acquire_instrumentation()
        try:
            yield result
        finally:
            _release_instrumentation()
    finally:
        _active_stack.reset(stack_token)
        _active_result.reset(result_token)


def _acquire_instrumentation() -> None:
    global _patch_depth, _original_send, _original_send_robust, _original_live_receivers, _signal_names
    signal_names = {id(signal): name for name, signal in discover_signal_objects().items()}
    with _patch_lock:
        if _patch_depth == 0:
            _original_send = Signal.send
            _original_send_robust = Signal.send_robust
            _original_live_receivers = Signal._live_receivers
            setattr(Signal, "send", _traced_send)
            setattr(Signal, "send_robust", _traced_send_robust)
            setattr(Signal, "_live_receivers", _traced_live_receivers)
        _patch_depth += 1
        _signal_names = signal_names


def _release_instrumentation() -> None:
    global _patch_depth, _original_send, _original_send_robust, _original_live_receivers
    with _patch_lock:
        _patch_depth -= 1
        if _patch_depth == 0:
            if _original_send is not None:
                setattr(Signal, "send", _original_send)
            if _original_send_robust is not None:
                setattr(Signal, "send_robust", _original_send_robust)
            if _original_live_receivers is not None:
                setattr(Signal, "_live_receivers", _original_live_receivers)
            _original_send = None
            _original_send_robust = None
            _original_live_receivers = None


def _traced_send(signal: Signal, sender: Any, **named: Any) -> Any:
    if _original_send is None:
        raise RuntimeError("SignalScope instrumentation is not initialized")
    responses = _original_send(signal, sender, **named)
    return _restore_receiver_identities(responses)


def _traced_send_robust(signal: Signal, sender: Any, **named: Any) -> Any:
    if _original_send_robust is None:
        raise RuntimeError("SignalScope instrumentation is not initialized")
    responses = _original_send_robust(signal, sender, **named)
    return _restore_receiver_identities(responses)


def _traced_live_receivers(signal: Signal, sender: Any) -> Any:
    if _original_live_receivers is None:
        raise RuntimeError("SignalScope instrumentation is not initialized")
    receivers = _original_live_receivers(signal, sender)
    if _active_result.get() is None:
        return receivers
    signal_name = _signal_names.get(id(signal), type(signal).__name__)
    if (
        isinstance(receivers, tuple)
        and len(receivers) == 2
        and all(isinstance(group, (list, tuple)) for group in receivers)
    ):
        return (
            [_wrap_receiver(receiver, signal_name, sender, False) for receiver in receivers[0]],
            [_wrap_receiver(receiver, signal_name, sender, True) for receiver in receivers[1]],
        )
    return [
        _wrap_receiver(receiver, signal_name, sender, iscoroutinefunction(receiver))
        for receiver in receivers
    ]


def _wrap_receiver(
    receiver: Callable[..., Any],
    signal_name: str,
    sender: Any,
    asynchronous: bool,
) -> Callable[..., Any]:
    if getattr(receiver, "_signalscope_original_receiver", None) is not None:
        return receiver
    if asynchronous:
        @wraps(receiver)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            result = _active_result.get()
            if result is None:
                return await receiver(*args, **kwargs)
            node, token = _start_node(result, signal_name, sender, receiver, kwargs)
            try:
                with _query_tracking(node):
                    return await receiver(*args, **kwargs)
            finally:
                _finish_node(node, token)

        setattr(async_wrapper, "_signalscope_original_receiver", receiver)
        return async_wrapper

    @wraps(receiver)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        result = _active_result.get()
        if result is None:
            return receiver(*args, **kwargs)
        node, token = _start_node(result, signal_name, sender, receiver, kwargs)
        try:
            with _query_tracking(node):
                return receiver(*args, **kwargs)
        finally:
            _finish_node(node, token)

    setattr(sync_wrapper, "_signalscope_original_receiver", receiver)
    return sync_wrapper


def _start_node(
    result: TraceResult,
    signal_name: str,
    sender: Any,
    receiver: Callable[..., Any],
    kwargs: dict[str, Any],
) -> tuple[TraceNode, Token[tuple[TraceNode, ...]]]:
    stack = _active_stack.get()
    node = TraceNode(
        signal=signal_name,
        sender=_object_label(sender),
        instance=_instance_label(kwargs.get("instance")),
        receiver=_receiver_label(receiver),
        depth=len(stack) + 1,
        start_time=time.perf_counter(),
        file=_receiver_file(receiver),
        line=_receiver_line(receiver),
    )
    if stack:
        stack[-1].children.append(node)
    else:
        result.roots.append(node)
    token = _active_stack.set((*stack, node))
    return node, token


def _finish_node(node: TraceNode, token: Token[tuple[TraceNode, ...]]) -> None:
    node.end_time = time.perf_counter()
    node.duration_ms = (node.end_time - node.start_time) * 1000
    _active_stack.reset(token)


@contextmanager
def _query_tracking(node: TraceNode) -> Generator[None, None, None]:
    def record_query(
        execute: Callable[..., Any],
        sql: str,
        params: Any,
        many: bool,
        context: dict[str, Any],
    ) -> Any:
        started = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            active_stack = _active_stack.get()
            if active_stack and active_stack[-1] is node:
                node.query_count += 1
                node.query_time_ms += (time.perf_counter() - started) * 1000
                fingerprint = hashlib.sha256(" ".join(sql.split()).encode("utf-8")).hexdigest()
                node.query_fingerprints[fingerprint] = node.query_fingerprints.get(fingerprint, 0) + 1

    with ExitStack() as stack:
        for connection in connections.all():
            stack.enter_context(connection.execute_wrapper(record_query))
        yield


def _restore_receiver_identities(responses: Any) -> Any:
    if isinstance(responses, list):
        for index, (receiver, response) in enumerate(responses):
            original = getattr(receiver, "_signalscope_original_receiver", receiver)
            if original is not receiver:
                responses[index] = (original, response)
    return responses


def _object_label(value: Any) -> str:
    meta = getattr(value, "_meta", None)
    if meta is not None:
        return str(meta.label)
    return f"{type(value).__module__}.{type(value).__qualname__}"


def _instance_label(instance: Any) -> str | None:
    if instance is None:
        return None
    meta = getattr(instance, "_meta", None)
    model_name = str(meta.label) if meta is not None else type(instance).__name__
    return f"{model_name}(pk={getattr(instance, 'pk', None)})"


def _receiver_label(receiver: Callable[..., Any]) -> str:
    module = getattr(receiver, "__module__", type(receiver).__module__)
    name = getattr(receiver, "__qualname__", type(receiver).__qualname__)
    return f"{module}.{name}"


def _receiver_file(receiver: Callable[..., Any]) -> str | None:
    try:
        return inspect.getsourcefile(receiver)
    except (TypeError, OSError):
        return None


def _receiver_line(receiver: Callable[..., Any]) -> int | None:
    code = getattr(receiver, "__code__", None)
    if code is not None:
        return code.co_firstlineno
    try:
        return inspect.getsourcelines(receiver)[1]
    except (TypeError, OSError):
        return None
