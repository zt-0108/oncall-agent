"""独立、可恢复的本地故障实验服务。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "fault_lab" / "scenarios" / "catalog.json"
EXTERNAL_CASES_PATH = ROOT / "fault_lab" / "datasets" / "external_cases.json"
LOG_PATH = Path(os.getenv("FAULT_LAB_LOG_PATH", str(ROOT / "logs" / "fault-lab.jsonl")))
LOG_MAX_BYTES = int(os.getenv("FAULT_LAB_LOG_MAX_BYTES", str(10 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.getenv("FAULT_LAB_LOG_BACKUP_COUNT", "5"))
SCENARIO_TTL_SECONDS = max(10, int(os.getenv("FAULT_LAB_SCENARIO_TTL_SECONDS", "300")))

REQUESTS = Counter(
    "fault_lab_http_requests_total",
    "Fault Lab HTTP requests.",
    ("method", "path", "status"),
)
DURATION = Histogram(
    "fault_lab_http_request_duration_seconds",
    "Fault Lab end-to-end request duration.",
    ("method", "path"),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10),
)
DEPENDENCY_FAILURES = Counter(
    "fault_lab_dependency_failures_total",
    "Fault Lab dependency failures.",
    ("dependency", "reason"),
)
DATABASE_FAILURES = Counter(
    "fault_lab_database_failures_total",
    "Fault Lab database failures.",
    ("database", "reason"),
)
SCENARIO_ACTIVE = Gauge(
    "fault_lab_scenario_active",
    "Currently active Fault Lab scenario (one-hot).",
    ("scenario",),
)
RESOURCE_PRESSURE = Gauge(
    "fault_lab_resource_pressure",
    "Synthetic resource pressure for a replay scenario, from 0 to 100.",
    ("service", "resource"),
)

_base_catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
_external_catalog = json.loads(EXTERNAL_CASES_PATH.read_text(encoding="utf-8"))
_catalog = {
    "version": max(_base_catalog["version"], _external_catalog["version"]),
    "scenarios": [*_base_catalog["scenarios"], *_external_catalog["cases"]],
    "external_sources": _external_catalog["sources"],
}
SCENARIOS = {item["id"]: item for item in _catalog["scenarios"]}
_state_lock = threading.RLock()
_log_lock = threading.Lock()
_active_scenario = "normal"
_request_sequence = 0
_scenario_deadline: float | None = None
_scenario_expires_at: datetime | None = None
SCENARIO_ACTIVE.labels(scenario="normal").set(1)
# 预注册零值序列，确保首次受控故障也能计算 rate/increase。
for _status in ("200", "500", "503", "504"):
    REQUESTS.labels(method="POST", path="/api/checkout", status=_status).inc(0)
DURATION.labels(method="POST", path="/api/checkout")
DATABASE_FAILURES.labels(database="orders", reason="connection_refused").inc(0)
DEPENDENCY_FAILURES.labels(dependency="payment", reason="timeout").inc(0)


def _update_resource_pressure(active_scenario: str) -> None:
    pressure_values = {"cpu": 95, "memory": 96, "disk_io": 97}
    for scenario in SCENARIOS.values():
        resource = scenario.get("resource")
        service = scenario.get("target_service")
        if not resource or not service:
            continue
        value = pressure_values.get(str(resource), 95) if scenario["id"] == active_scenario else 0
        RESOURCE_PRESSURE.labels(service=str(service), resource=str(resource)).set(value)


_update_resource_pressure("normal")


class FaultMetricsMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("path") == "/metrics":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500
        recorded = False

        def record() -> None:
            nonlocal recorded
            if recorded:
                return
            recorded = True
            route = scope.get("route")
            path = str(getattr(route, "path", "__unmatched__"))
            method = str(scope.get("method", "UNKNOWN"))
            REQUESTS.labels(method=method, path=path, status=str(status)).inc()
            DURATION.labels(method=method, path=path).observe(time.perf_counter() - started)

        async def wrapped_send(message: dict[str, Any]) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                status = int(message.get("status", 500))
            if message.get("type") == "http.response.body" and not message.get("more_body", False):
                record()
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        except BaseException:
            record()
            raise


app = FastAPI(title="Fault Lab", version="1.0.0")
app.add_middleware(FaultMetricsMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:9900", "http://localhost:9900"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


_SENSITIVE_KEYS = (
    "authorization",
    "token",
    "api_key",
    "apikey",
    "password",
    "secret",
    "cookie",
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[^\s,;]+"),
    re.compile(r"(?i)\b(api[_-]?key|token|password|secret)=([^\s,;]+)"),
)


def _redact(value: Any, key: str = "") -> Any:
    if any(part in key.lower() for part in _SENSITIVE_KEYS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            item_key: _redact(item_value, str(item_key)) for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(
                lambda match: (
                    f"{match.group(1)}=[REDACTED]" if match.lastindex else "Bearer [REDACTED]"
                ),
                redacted,
            )
        return redacted
    return value


def _rotate_log_if_needed(incoming_bytes: int) -> None:
    if LOG_MAX_BYTES <= 0 or not LOG_PATH.exists():
        return
    if LOG_PATH.stat().st_size + incoming_bytes <= LOG_MAX_BYTES:
        return
    if LOG_BACKUP_COUNT <= 0:
        LOG_PATH.unlink(missing_ok=True)
        return
    Path(f"{LOG_PATH}.{LOG_BACKUP_COUNT}").unlink(missing_ok=True)
    for index in range(LOG_BACKUP_COUNT - 1, 0, -1):
        source = Path(f"{LOG_PATH}.{index}")
        if source.exists():
            source.replace(Path(f"{LOG_PATH}.{index + 1}"))
    LOG_PATH.replace(Path(f"{LOG_PATH}.1"))


def _append_log(**payload: Any) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = _redact(
        {"timestamp": _now(), "service": "fault-lab", "data_origin": "live_local", **payload}
    )
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
    encoded_size = len((line + "\n").encode("utf-8"))
    with _log_lock:
        _rotate_log_if_needed(encoded_size)
        with LOG_PATH.open("a", encoding="utf-8") as output:
            output.write(line + "\n")


def _expire_scenario_if_needed() -> None:
    global _active_scenario, _request_sequence, _scenario_deadline, _scenario_expires_at
    expired_scenario: str | None = None
    with _state_lock:
        if _scenario_deadline is None or time.monotonic() < _scenario_deadline:
            return
        expired_scenario = _active_scenario
        _active_scenario = "normal"
        _request_sequence = 0
        _scenario_deadline = None
        _scenario_expires_at = None
        SCENARIO_ACTIVE.labels(scenario=expired_scenario).set(0)
        SCENARIO_ACTIVE.labels(scenario="normal").set(1)
        _update_resource_pressure("normal")
    _append_log(
        level="INFO",
        event="scenario_auto_reset",
        scenario_id=expired_scenario,
        message="Fault Lab 场景达到安全时限，已自动恢复正常",
    )


def _state() -> dict[str, Any]:
    _expire_scenario_if_needed()
    with _state_lock:
        scenario = SCENARIOS[_active_scenario]
        sequence = _request_sequence
        expires_at = _scenario_expires_at
        deadline = _scenario_deadline
    return {
        "active_scenario": scenario,
        "request_sequence": sequence,
        "data_origin": "live_local",
        "log_path": str(LOG_PATH),
        "expires_at": expires_at.isoformat() if expires_at else None,
        "remaining_seconds": max(0, round(deadline - time.monotonic())) if deadline else None,
        "scenario_ttl_seconds": SCENARIO_TTL_SECONDS,
    }


def _set_scenario(scenario_id: str) -> dict[str, Any]:
    global _active_scenario, _request_sequence, _scenario_deadline, _scenario_expires_at
    if scenario_id not in SCENARIOS:
        raise HTTPException(status_code=404, detail="未知故障场景")
    with _state_lock:
        previous = _active_scenario
        _active_scenario = scenario_id
        _request_sequence = 0
        if scenario_id == "normal":
            _scenario_deadline = None
            _scenario_expires_at = None
        else:
            _scenario_deadline = time.monotonic() + SCENARIO_TTL_SECONDS
            _scenario_expires_at = datetime.now(UTC) + timedelta(seconds=SCENARIO_TTL_SECONDS)
        SCENARIO_ACTIVE.labels(scenario=previous).set(0)
        SCENARIO_ACTIVE.labels(scenario=scenario_id).set(1)
        _update_resource_pressure(scenario_id)
    _append_log(
        level="INFO",
        event="scenario_activated",
        scenario_id=scenario_id,
        message=f"Fault Lab 场景已切换为 {SCENARIOS[scenario_id]['title']}",
    )
    return _state()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", **_state()}


@app.get("/api/scenarios")
async def scenarios() -> dict[str, Any]:
    return {
        "version": _catalog["version"],
        "data_origin": "live_local",
        "scenarios": list(SCENARIOS.values()),
    }


@app.get("/api/state")
async def state() -> dict[str, Any]:
    return _state()


@app.post("/api/scenarios/{scenario_id}/activate")
async def activate(scenario_id: str) -> dict[str, Any]:
    return _set_scenario(scenario_id)


@app.post("/api/reset")
async def reset() -> dict[str, Any]:
    return _set_scenario("normal")


@app.post("/api/checkout")
async def checkout(request: Request) -> JSONResponse:
    global _request_sequence
    _expire_scenario_if_needed()
    trace_id = request.headers.get("x-trace-id") or uuid4().hex
    started = time.perf_counter()
    with _state_lock:
        scenario_id = _active_scenario
        scenario = SCENARIOS[scenario_id]
        profile = str(scenario.get("simulation_profile", scenario_id))
        _request_sequence += 1
        sequence = _request_sequence

    status = 200
    level = "INFO"
    event = "checkout_completed"
    message = "结账请求处理成功"
    error_type: str | None = None

    if profile == "database-error":
        status = 500
        level = "ERROR"
        event = "database_connection_refused"
        error_type = "DatabaseConnectionError"
        message = "订单数据库连接被拒绝"
        DATABASE_FAILURES.labels(database="orders", reason="connection_refused").inc()
    elif profile == "dependency-timeout":
        await asyncio.sleep(2.5)
        status = 504
        level = "ERROR"
        event = "payment_dependency_timeout"
        error_type = "DependencyTimeout"
        message = "支付服务调用超过 2.5 秒超时"
        DEPENDENCY_FAILURES.labels(dependency="payment", reason="timeout").inc()
    elif profile == "high-latency":
        await asyncio.sleep(2.5)
        message = "结账请求成功，但业务处理耗时异常"
    elif profile == "intermittent-500" and sequence % 2 == 0:
        status = 500
        level = "ERROR"
        event = "intermittent_handler_failure"
        error_type = "IntermittentHandlerError"
        message = "结账处理器发生间歇性异常"
    elif profile == "cpu_saturation":
        await asyncio.sleep(2.5)
        event = "cpu_saturation_detected"
        message = "checkoutservice CPU saturation; request processing exceeded 2.5 seconds"
    elif profile == "memory_pressure":
        if sequence % 2 == 0:
            status = 500
            level = "ERROR"
            event = "memory_allocation_failed"
            error_type = "MemoryError"
            message = "cartservice memory allocation failed near resource limit"
        else:
            event = "memory_pressure_detected"
            message = "cartservice memory working set reached 96 percent"
    elif profile == "network_delay":
        await asyncio.sleep(2.5)
        status = 504
        level = "ERROR"
        event = "downstream_network_delay"
        error_type = "DependencyTimeout"
        message = "paymentservice network delay exceeded checkout timeout"
        DEPENDENCY_FAILURES.labels(
            dependency=str(scenario.get("target_service", "paymentservice")),
            reason="timeout",
        ).inc()
    elif profile == "packet_loss" and sequence % 2 == 0:
        status = 503
        level = "ERROR"
        event = "downstream_packet_loss"
        error_type = "NetworkPacketLoss"
        message = "recommendationservice unavailable because simulated packets were lost"
    elif profile == "socket_exhaustion":
        status = 503
        level = "ERROR"
        event = "socket_pool_exhausted"
        error_type = "OSError"
        message = "emailservice connection pool exhausted: Too many open files"
    elif profile == "disk_io":
        await asyncio.sleep(2.5)
        event = "disk_io_saturation"
        message = "orderservice order persistence blocked by saturated disk I/O"

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    await asyncio.to_thread(
        _append_log,
        level=level,
        event=event,
        scenario_id=scenario_id,
        scenario_provenance=scenario.get("data_origin", "live_local"),
        benchmark=scenario.get("benchmark"),
        source_case_pattern=scenario.get("source_case_pattern"),
        simulation_profile=profile,
        target_service=scenario.get("target_service", "fault-lab"),
        trace_id=trace_id,
        status=status,
        duration_ms=duration_ms,
        error_type=error_type,
        message=message,
    )
    return JSONResponse(
        status_code=status,
        content={
            "success": status < 400,
            "service": "fault-lab",
            "scenario_id": scenario_id,
            "trace_id": trace_id,
            "status": status,
            "duration_ms": duration_ms,
            "message": message,
        },
    )


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    _expire_scenario_if_needed()
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=9910)
