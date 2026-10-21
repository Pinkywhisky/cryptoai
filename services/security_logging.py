"""Diagnostic events with no request bodies, URLs, exception messages or credentials."""

import json
import logging
from datetime import UTC, datetime

from starlette.routing import Match

logger = logging.getLogger("crypto_ai.security")


def route_template(request):
    if request is None:
        return "<unmatched>"
    route = request.scope.get("route")
    path = getattr(route, "path", None) or getattr(request.state, "route_template", None)
    if path:
        return path
    # Matching does not execute endpoints or expose captured path parameters.
    partial = None
    try:
        for candidate in getattr(request.scope.get("app"), "routes", ()):
            if not getattr(candidate, "path", None):
                continue
            match, _ = candidate.matches(request.scope)
            if match == Match.FULL:
                return candidate.path
            if match == Match.PARTIAL and partial is None:
                partial = candidate.path
    except Exception:
        pass  # Diagnostics must never turn a security rejection into an error.
    return partial or (request.scope.get("path") if request.scope.get("path") in {"/login", "/logout"} else "<unmatched>")


def security_event(request, event: str, status: int, exc: Exception | None = None):
    path = route_template(request)
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": event,
        "route": path,
        "method": request.method if request is not None and request.method in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"} else "OTHER",
        "status": status,
        "request_id": getattr(request.state, "request_id", None) if request is not None else None,
    }
    if exc is not None:
        record["exception_type"] = type(exc).__name__
    logger.log(logging.ERROR if status >= 500 else logging.INFO, "%s", json.dumps(record, ensure_ascii=True))
