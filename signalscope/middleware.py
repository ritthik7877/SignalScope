"""Opt-in request tracing middleware for Django."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponseBase

from signalscope.risks import detect_risks
from signalscope.tracer import trace_signals


class SignalScopeMiddleware:
    """Trace requests explicitly opted in with a header or query parameter."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        """Store Django's next middleware or view callable."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        """Return the response, adding trace summaries only for opted-in requests."""
        if not self._is_enabled() or not self._is_requested(request):
            return self.get_response(request)

        with trace_signals() as trace:
            response = self.get_response(request)

        risks = detect_risks(trace)
        response["X-SignalScope-Receivers"] = str(len(trace.flatten()))
        response["X-SignalScope-Risks"] = str(len(risks))
        return response

    @staticmethod
    def _is_enabled() -> bool:
        config: Any = getattr(settings, "SIGNALSCOPE", {})
        return bool(settings.DEBUG and config.get("ENABLED", settings.DEBUG))

    @staticmethod
    def _is_requested(request: HttpRequest) -> bool:
        return (
            request.META.get("HTTP_X_SIGNALSCOPE") == "1"
            or request.GET.get("signalscope") == "1"
        )
