"""使用真实 Prometheus 数据的监控 MCP Server。"""

from __future__ import annotations

import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("Monitor_MCP_Server")
BASE_URL = os.getenv("PROMETHEUS_BASE_URL", "http://127.0.0.1:9090").rstrip("/")
TIMEOUT = float(os.getenv("PROMETHEUS_REQUEST_TIMEOUT", "10"))
DURATION = re.compile(r"^[1-9][0-9]*[smhdwy]$")
SERVICE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
mcp = FastMCP("Monitor")


def _get(path: str, params: dict[str, str]) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=TIMEOUT, trust_env=False) as client:
            response = client.get(f"{BASE_URL}{path}", params=params)
            response.raise_for_status()
            body = cast(dict[str, Any], response.json())
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"Prometheus 查询失败: {exc}") from exc
    if body.get("status") != "success":
        raise RuntimeError(f"Prometheus 返回失败: {body.get('error', 'unknown error')}")
    return body


def _query(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 2000:
        raise ValueError("PromQL 不能为空且不能超过 2000 个字符")
    return value


def _duration(value: str, name: str) -> str:
    value = value.strip()
    if not DURATION.fullmatch(value):
        raise ValueError(f"{name} 必须是 30s、5m、1h 形式的 Prometheus 时长")
    return value


def _time(value: str | None, default: datetime) -> str:
    if not value:
        return str(default.timestamp())
    try:
        return str(float(value))
    except ValueError:
        pass
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("时间必须是 Unix 时间戳或 ISO-8601") from exc
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return str(result.timestamp())


def _result(body: dict[str, Any]) -> list[dict[str, Any]]:
    return (body.get("data") or {}).get("result") or []


def _scalar(body: dict[str, Any]) -> float | None:
    result = _result(body)
    if not result:
        return None
    sample = result[0].get("value") or []
    try:
        value = float(sample[1])
        return value if math.isfinite(value) else None
    except (IndexError, TypeError, ValueError):
        return None


@mcp.tool()
def query_prometheus(query: str, evaluation_time: str | None = None) -> dict[str, Any]:
    """执行真实 PromQL 即时查询。适合查询当前请求率、错误率、延迟及进程资源指标。"""
    params = {"query": _query(query)}
    if evaluation_time:
        params["time"] = _time(evaluation_time, datetime.now(timezone.utc))
    body = _get("/api/v1/query", params)
    data = body.get("data") or {}
    return {"source": BASE_URL, "query": query, "result_type": data.get("resultType"), "result": _result(body)}


@mcp.tool()
def query_prometheus_range(
    query: str,
    start_time: str | None = None,
    end_time: str | None = None,
    step: str = "30s",
) -> dict[str, Any]:
    """执行真实 PromQL 区间查询，用于查看故障发生前后的指标趋势。"""
    now = datetime.now(timezone.utc)
    params = {
        "query": _query(query),
        "start": _time(start_time, now - timedelta(hours=1)),
        "end": _time(end_time, now),
        "step": _duration(step, "step"),
    }
    body = _get("/api/v1/query_range", params)
    data = body.get("data") or {}
    return {"source": BASE_URL, "query": query, "result_type": data.get("resultType"), "result": _result(body)}


@mcp.tool()
def query_service_http_summary(
    service_name: str = "oncall-agent",
    window: str = "5m",
) -> dict[str, Any]:
    """查询服务真实 HTTP 请求率、5xx 比例、P95 延迟及进程指标；无数据时返回 null。"""
    service = service_name.strip()
    if not SERVICE.fullmatch(service):
        raise ValueError("service_name 含有不允许的字符")
    window = _duration(window, "window")
    label = f'service="{service}"'
    if service == "fault-lab":
        request_metric = "fault_lab_http_requests_total"
        duration_metric = "fault_lab_http_request_duration_seconds_bucket"
        http_selector = f'{label},path="/api/checkout"'
    else:
        request_metric = "oncall_agent_http_requests_total"
        duration_metric = "oncall_agent_http_request_duration_seconds_bucket"
        http_selector = label
    queries = {
        "requests_per_second": f"sum(rate({request_metric}{{{http_selector}}}[{window}]))",
        "http_5xx_ratio": (
            f"sum(rate({request_metric}{{{http_selector},status=~\"5..\"}}[{window}])) / "
            f"clamp_min(sum(rate({request_metric}{{{http_selector}}}[{window}])), 0.001)"
        ),
        "p95_latency_seconds": (
            "histogram_quantile(0.95, sum by (le) "
            f"(rate({duration_metric}{{{http_selector}}}[{window}])))"
        ),
        "process_cpu_percent": f"sum(rate(process_cpu_seconds_total{{{label}}}[{window}])) * 100",
        "resident_memory_bytes": f"sum(process_resident_memory_bytes{{{label}}})",
    }
    values = {
        name: _scalar(_get("/api/v1/query", {"query": promql}))
        for name, promql in queries.items()
    }
    return {
        "source": BASE_URL,
        "service_name": service,
        "window": window,
        "values": values,
        "queries": queries,
        "note": "null 表示 Prometheus 中没有对应的真实时间序列",
    }

if __name__ == "__main__":
    logger.info("Monitor MCP 连接 Prometheus: %s", BASE_URL)
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8004, path="/mcp")