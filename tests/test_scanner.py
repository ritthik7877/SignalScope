from __future__ import annotations

from pathlib import Path

from signalscope.scanner import ReceiverRecord, scan_app_paths


def test_scans_receiver_decorator_and_preserves_sender_and_location(tmp_path: Path) -> None:
    app = tmp_path / "decorated_app"
    app.mkdir()
    source = app / "signals.py"
    source.write_text(
        "from django.dispatch import receiver\n"
        "@receiver(post_save, sender=Order)\n"
        "def update_order(sender, instance, **kwargs):\n"
        "    pass\n",
        encoding="utf-8",
    )

    records = scan_app_paths([app])

    assert records == [
        ReceiverRecord(
            signal="post_save",
            sender="Order",
            receiver="update_order",
            file=str(source.resolve()),
            line=2,
        )
    ]


def test_scans_connect_form_with_dotted_custom_signal_and_string_sender(tmp_path: Path) -> None:
    app = tmp_path / "connected_app"
    app.mkdir()
    source = app / "signals.py"
    source.write_text(
        "from django.db.models.signals import post_save\n"
        "post_save.connect(handle_save, sender='missing_app.Ghost')\n"
        "custom_signal.connect(namespace.handler)\n",
        encoding="utf-8",
    )

    records = scan_app_paths([app])

    assert [(record.signal, record.sender, record.receiver, record.line) for record in records] == [
        ("post_save", "missing_app.Ghost", "handle_save", 2),
        ("custom_signal", None, "namespace.handler", 3),
    ]


def test_scans_installed_app_example_receivers() -> None:
    from signalscope.scanner import scan_installed_apps

    records = scan_installed_apps()

    assert any(record.receiver == "update_stock" and record.signal == "post_save" for record in records)
    assert any(record.receiver == "step_one" and record.signal == "workflow_start" for record in records)
    assert any(record.receiver == "orphan_receiver" and record.sender == "missing_app.Ghost" for record in records)
