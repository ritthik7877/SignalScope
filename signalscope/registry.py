"""Read live Django signal receivers and merge them with static discoveries."""

from __future__ import annotations

import inspect
import sys
import weakref
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Callable, TypeAlias

from django.apps import apps
from django.db.models import Model
from django.dispatch import Signal
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete, pre_save

from signalscope.scanner import ReceiverRecord, scan_installed_apps

Receiver: TypeAlias = Callable[..., Any]
Sender: TypeAlias = type[Model] | str | None
ReceiverMap: TypeAlias = dict[Sender, dict[str, list["ReceiverInfo"]]]


@dataclass(frozen=True)
class LiveReceiverRecord:
    """A receiver resolved from a live Django signal object."""

    signal: str
    sender: type[Model] | None
    receiver: Receiver
    is_async: bool


@dataclass(frozen=True)
class ReceiverInfo:
    """A static or live receiver entry in the merged map."""

    signal: str
    sender: Sender
    receiver: str
    file: str | None
    line: int | None
    is_live: bool
    is_async: bool


def discover_signal_objects() -> dict[str, Signal]:
    """Find built-in signals and custom Signal objects exposed by loaded app modules."""
    signals: dict[str, Signal] = {
        "pre_save": pre_save,
        "post_save": post_save,
        "pre_delete": pre_delete,
        "post_delete": post_delete,
        "m2m_changed": m2m_changed,
    }
    app_names = tuple(config.name for config in apps.get_app_configs())
    for module_name, module in sorted(sys.modules.items()):
        if module is None or not any(
            module_name == app_name or module_name.startswith(f"{app_name}.")
            for app_name in app_names
        ):
            continue
        for attribute_name, value in vars(module).items():
            if not isinstance(value, Signal):
                continue
            if any(existing is value for existing in signals.values()):
                continue
            if attribute_name not in signals:
                signals[attribute_name] = value
            else:
                qualified_name = f"{module_name}.{attribute_name}"
                signals[qualified_name] = value
    return signals


def read_live_receivers(
    signal_objects: Mapping[str, Signal] | None = None,
) -> list[LiveReceiverRecord]:
    """Resolve currently connected receivers, tolerating Django receiver tuple variants."""
    selected_signals = signal_objects if signal_objects is not None else discover_signal_objects()
    model_by_sender_id = {id(model): model for model in apps.get_models()}
    records: list[LiveReceiverRecord] = []
    seen: set[tuple[int, str, int]] = set()

    for signal_name, signal in selected_signals.items():
        for entry in signal.receivers:
            receiver_ref, sender_id, is_async = _unpack_receiver(entry)
            receiver = _resolve_receiver(receiver_ref)
            if receiver is None:
                continue
            key = (id(signal), signal_name, id(receiver))
            if key in seen:
                continue
            seen.add(key)
            sender = model_by_sender_id.get(sender_id) if sender_id is not None else None
            records.append(
                LiveReceiverRecord(
                    signal=signal_name,
                    sender=sender,
                    receiver=receiver,
                    is_async=is_async,
                )
            )
    return records


def build_receiver_map(
    static_records: Iterable[ReceiverRecord] | None = None,
    live_receivers: Iterable[LiveReceiverRecord] | None = None,
) -> ReceiverMap:
    """Merge static and live receiver data into a sender -> signal -> receivers map."""
    static_items = list(scan_installed_apps() if static_records is None else static_records)
    live_items = list(read_live_receivers() if live_receivers is None else live_receivers)
    result: ReceiverMap = {}

    for record in static_items:
        sender = _resolve_static_sender(record.sender)
        info = ReceiverInfo(
            signal=record.signal,
            sender=sender,
            receiver=record.receiver,
            file=record.file,
            line=record.line,
            is_live=False,
            is_async=False,
        )
        _add_receiver(result, sender, record.signal, info)

    for live in live_items:
        sender = live.sender
        receiver_name = _receiver_name(live.receiver)
        matching = _find_static_match(static_items, live.signal, sender, receiver_name)
        info = ReceiverInfo(
            signal=live.signal,
            sender=sender,
            receiver=matching.receiver if matching is not None else receiver_name,
            file=matching.file if matching is not None else _receiver_file(live.receiver),
            line=matching.line if matching is not None else None,
            is_live=True,
            is_async=live.is_async,
        )
        _add_receiver(result, sender, live.signal, info)

    return result


def _unpack_receiver(entry: tuple[Any, ...]) -> tuple[Any, int | None, bool]:
    if len(entry) not in (2, 3):
        return None, None, False
    lookup_key, receiver_ref = entry[0], entry[1]
    sender_id = lookup_key[1] if isinstance(lookup_key, tuple) and len(lookup_key) > 1 else None
    is_async = bool(entry[2]) if len(entry) == 3 else False
    return receiver_ref, sender_id if isinstance(sender_id, int) else None, is_async


def _resolve_receiver(receiver_ref: Any) -> Receiver | None:
    if isinstance(receiver_ref, weakref.ReferenceType):
        receiver = receiver_ref()
    else:
        receiver = receiver_ref
    return receiver if callable(receiver) else None


def _resolve_static_sender(sender_name: str | None) -> Sender:
    if sender_name is None:
        return None
    normalized = sender_name.rsplit(".", 1)[-1]
    matching = [
        model
        for model in apps.get_models()
        if normalized in {model.__name__, model._meta.label, model._meta.label_lower}
    ]
    return matching[0] if len(matching) == 1 else sender_name


def _find_static_match(
    static_records: list[ReceiverRecord],
    signal_name: str,
    sender: type[Model] | None,
    receiver_name: str,
) -> ReceiverRecord | None:
    for record in static_records:
        if record.signal != signal_name:
            continue
        static_sender = _resolve_static_sender(record.sender)
        if static_sender is not None and static_sender is not sender:
            continue
        if (
            record.receiver == receiver_name
            or receiver_name.endswith(f".{record.receiver}")
            or record.receiver.endswith(f".{receiver_name}")
        ):
            return record
    return None


def _receiver_name(receiver: Receiver) -> str:
    return f"{receiver.__module__}.{receiver.__qualname__}"


def _receiver_file(receiver: Receiver) -> str | None:
    try:
        return inspect.getsourcefile(receiver)
    except (TypeError, OSError):
        return None


def _add_receiver(
    receiver_map: ReceiverMap,
    sender: Sender,
    signal_name: str,
    receiver: ReceiverInfo,
) -> None:
    receivers = receiver_map.setdefault(sender, {}).setdefault(signal_name, [])
    for index, existing in enumerate(receivers):
        if existing.receiver == receiver.receiver:
            if receiver.is_live:
                receivers[index] = receiver
            return
    receivers.append(receiver)
