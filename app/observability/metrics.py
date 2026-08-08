"""Prometheus 指标定义与低开销 ASGI 请求埋点。"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "oncall_agent_http_requests_total",
    "Total HTTP requests handled by OnCall Agent.",
    ("method", "path", "status"),
)
HTTP_REQUEST_DURATION = Histogram(
    "oncall_agent_http_request_duration_seconds",
    "End-to-end HTTP request duration in seconds.",
    ("method", "path"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
AIOPS_DIAGNOSES = Counter(
    "oncall_agent_aiops_diagnoses_total",
    "AIOps diagnosis runs grouped by mode and final status.",
    ("mode", "status"),
)

Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[Any]], Send], Awaitable[None]]


class MetricsMiddleware:
    """在响应流真正结束时记录状态码和端到端耗时。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Send,
    ) -> None:
        if scope.get("type") != "http" or scope.get("path") == "/metrics":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500
        recorded = False

        def route_path() -> str:
            route = scope.get("route")
            template = getattr(route, "path", None)
            return str(template) if template else "__unmatched__"

        def record() -> None:
            nonlocal recorded
            if recorded:
                return
            recorded = True
            method = str(scope.get("method", "UNKNOWN"))
            path = route_path()
            HTTP_REQUESTS.labels(method=method, path=path, status=str(status_code)).inc()
            HTTP_REQUEST_DURATION.labels(method=method, path=path).observe(
                time.perf_counter() - started
            )

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
            if message.get("type") == "http.response.body" and not message.get("more_body", False):
                record()
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except BaseException:
            record()
            raise


def record_aiops_diagnosis(mode: str, status: str) -> None:
    AIOPS_DIAGNOSES.labels(mode=mode, status=status).inc()
