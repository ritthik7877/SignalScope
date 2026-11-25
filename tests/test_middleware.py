from __future__ import annotations

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.dispatch import Signal

from signalscope.middleware import SignalScopeMiddleware


@pytest.mark.parametrize(
    ("query", "meta"),
    [
        ("", {"HTTP_X_SIGNALSCOPE": "1"}),
        ("?signalscope=1", {}),
    ],
)
def test_traces_opted_in_requests_and_adds_summary_headers(query: str, meta: dict[str, str]) -> None:
    signal = Signal()

    def receiver(sender: object, **kwargs: object) -> str:
        return "ok"

    signal.connect(receiver, weak=False)
    factory = RequestFactory()
    request = factory.get(f"/{query}", **meta)
    middleware = SignalScopeMiddleware(
        lambda current_request: (signal.send(sender=object), HttpResponse("ok"))[1]
    )

    with override_settings(
        DEBUG=True,
        SIGNALSCOPE={"ENABLED": True, "MAX_QUERIES": 5, "SLOW_MS": 100, "MAX_DEPTH": 3},
    ):
        response = middleware(request)

    assert response["X-SignalScope-Receivers"] == "1"
    assert response["X-SignalScope-Risks"] == "1"


@pytest.mark.parametrize(
    "config",
    [
        {"DEBUG": False, "SIGNALSCOPE": {"ENABLED": True}},
        {"DEBUG": True, "SIGNALSCOPE": {"ENABLED": False}},
    ],
)
def test_disabled_middleware_does_not_add_headers(config: dict[str, object]) -> None:
    factory = RequestFactory()
    request = factory.get("/", HTTP_X_SIGNALSCOPE="1")
    response = HttpResponse("not traced")
    calls = 0

    def get_response(current_request: object) -> HttpResponse:
        nonlocal calls
        calls += 1
        return response

    middleware = SignalScopeMiddleware(get_response)
    with override_settings(**config):
        result = middleware(request)

    assert result is response
    assert calls == 1
    assert "X-SignalScope-Receivers" not in result
