"""Build receiver maps and trace callable entry points."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string

from signalscope.registry import build_receiver_map, read_live_receivers
from signalscope.report import build_report_data, format_text_report, render_html_report
from signalscope.risks import detect_risks
from signalscope.scanner import scan_installed_apps
from signalscope.tracer import TraceResult, trace_signals


class Command(BaseCommand):
    """Inspect installed signal receivers or trace a dotted callable."""

    help = "Map Django signal receivers and report runtime signal risks."

    def add_arguments(self, parser: Any) -> None:
        """Register map, trace, formatting, output, and CI options."""
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--map", action="store_true", help="Build a static and live receiver map.")
        mode.add_argument("--trace", metavar="DOTTED_CALLABLE", help="Run and trace a zero-argument callable.")
        parser.add_argument(
            "--format",
            choices=("text", "json", "html"),
            default="text",
            help="Output format.",
        )
        parser.add_argument("--output", help="Write the report to this file.")
        parser.add_argument(
            "--fail-on-risk",
            action="store_true",
            help="Exit with an error after reporting if any risks were detected.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Generate the selected receiver report and optionally fail on risks."""
        trace_result = self._trace_callable(options["trace"]) if options["trace"] else None
        static_records = scan_installed_apps()
        receiver_map = build_receiver_map(
            static_records=static_records,
            live_receivers=read_live_receivers(),
        )
        risks = detect_risks(trace_result, static_records=static_records)

        report_format = options["format"]
        if report_format == "json":
            output = json.dumps(
                build_report_data(receiver_map, trace_result, risks),
                ensure_ascii=False,
                indent=2,
            )
        elif report_format == "html":
            output = render_html_report(receiver_map, trace_result, risks)
        else:
            output = format_text_report(receiver_map, trace_result, risks)

        if options["output"]:
            Path(options["output"]).write_text(output, encoding="utf-8")
        else:
            self.stdout.write(output)

        if options["fail_on_risk"] and risks:
            raise CommandError(f"SignalScope detected {len(risks)} risk(s).")

    @staticmethod
    def _trace_callable(dotted_path: str) -> TraceResult:
        target = import_string(dotted_path)
        if not callable(target):
            raise CommandError(f"{dotted_path!r} does not resolve to a callable.")
        with trace_signals() as trace:
            result = target()
            if inspect.isawaitable(result):
                asyncio.run(_await_result(result))
        return trace


async def _await_result(awaitable: Awaitable[Any]) -> Any:
    return await awaitable
