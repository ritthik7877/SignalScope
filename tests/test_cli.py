from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command, CommandError


def test_cli_supports_text_json_and_html_formats() -> None:
    text_output = StringIO()
    call_command("signalscope", "--map", "--format", "text", stdout=text_output)
    assert "SignalScope receiver map" in text_output.getvalue()
    assert "orphan_receiver" in text_output.getvalue()

    json_output = StringIO()
    call_command("signalscope", "--map", "--format", "json", stdout=json_output)
    parsed = json.loads(json_output.getvalue())
    assert any(receiver["receiver"] == "orphan_receiver" for receiver in parsed["receivers"])

    html_output = StringIO()
    call_command("signalscope", "--map", "--format", "html", stdout=html_output)
    assert "<title>SignalScope report</title>" in html_output.getvalue()
    assert "Traced signal chain" in html_output.getvalue()


def test_cli_traces_dotted_callable_and_writes_output_file(tmp_path: Path) -> None:
    output_file = tmp_path / "report.json"

    call_command(
        "signalscope",
        "--trace",
        "demo_project.trace_example.trace_workflow",
        "--format",
        "json",
        "--output",
        str(output_file),
    )

    report = json.loads(output_file.read_text(encoding="utf-8"))
    assert any(item["receiver"].endswith(".step_four") for item in report["trace"])


def test_cli_fail_on_risk_returns_a_management_error() -> None:
    with pytest.raises(CommandError, match="risk"):
        call_command("signalscope", "--map", "--fail-on-risk", stdout=StringIO())
