"""查询本地真实结构化运行日志的 MCP Server。

保留 8003 端口和 cls 配置键以兼容现有项目，但不再声称连接腾讯云 CLS。
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter, deque
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()
ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = Path(os.getenv("FAULT_LAB_LOG_PATH", str(ROOT / "logs" / "fault-lab.jsonl")))
MAX_SCAN_LINES = int(os.getenv("LOG_MCP_MAX_SCAN_LINES", "10000"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("Log_MCP_Server")
mcp = FastMCP("Logs")


def _read_logs() -> list[dict[str, Any]]:
    if not LOG_PATH.exists():
        return []
    rows: deque[str] = deque(maxlen=MAX_SCAN_LINES)
    with LOG_PATH.open("r", encoding="utf-8") as source:
        for line in source:
            if line.strip():
                rows.append(line)
    result: list[dict[str, Any]] = []
    for line in rows:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            result.append(item)
    return result


def _matches(
    item: dict[str, Any],
    service_name: str | None,
    log_level: str | None,
    keyword: str | None,
    trace_id: str | None,
    scenario_id: str | None,
    start_time: str | None,
    end_time: str | None,
) -> bool:
    if service_name and item.get("service") != service_name:
        return False
    if log_level and str(item.get("level", "")).upper() != log_level.upper():
        return False
    if trace_id and item.get("trace_id") != trace_id:
        return False
    if scenario_id and item.get("scenario_id") != scenario_id:
        return False
    timestamp = str(item.get("timestamp", ""))
    if start_time and timestamp < start_time:
        return False
    if end_time and timestamp > end_time:
        return False
    if keyword:
        haystack = json.dumps(item, ensure_ascii=False).lower()
        if keyword.lower() not in haystack:
            return False
    return True


@mcp.tool()
def list_log_sources() -> dict[str, Any]:
    """列出当前真实日志后端、文件位置和可查询字段。"""
    rows = _read_logs()
    services = sorted({str(item.get("service")) for item in rows if item.get("service")})
    return {
        "backend": "live_local",
        "data_origin": "live_local",
        "path": str(LOG_PATH),
        "available": LOG_PATH.exists(),
        "scanned_lines": len(rows),
        "services": services,
        "fields": ["timestamp", "service", "level", "event", "scenario_id", "trace_id", "status", "duration_ms", "error_type", "message"],
    }


@mcp.tool()
def search_logs(
    service_name: str | None = None,
    log_level: str | None = None,
    keyword: str | None = None,
    trace_id: str | None = None,
    scenario_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """查询 Fault Lab 真实 JSONL 日志，可按服务、级别、关键词、trace_id、场景和 ISO 时间范围过滤。"""
    if limit < 1 or limit > 500:
        raise ValueError("limit 必须在 1 到 500 之间")
    matches = [
        item
        for item in _read_logs()
        if _matches(item, service_name, log_level, keyword, trace_id, scenario_id, start_time, end_time)
    ]
    matches.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
    return {
        "backend": "live_local",
        "data_origin": "live_local",
        "total": len(matches),
        "logs": matches[:limit],
        "truncated": len(matches) > limit,
    }


@mcp.tool()
def analyze_log_patterns(
    service_name: str = "fault-lab",
    scenario_id: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """聚合真实日志中的级别、事件、错误类型、状态码和 trace_id，辅助根因分析。"""
    rows = [
        item for item in _read_logs()
        if item.get("service") == service_name and (not scenario_id or item.get("scenario_id") == scenario_id)
    ][-limit:]
    return {
        "backend": "live_local",
        "data_origin": "live_local",
        "service_name": service_name,
        "scenario_id": scenario_id,
        "sample_size": len(rows),
        "levels": dict(Counter(str(item.get("level", "UNKNOWN")) for item in rows)),
        "events": dict(Counter(str(item.get("event", "unknown")) for item in rows)),
        "error_types": dict(Counter(str(item.get("error_type")) for item in rows if item.get("error_type"))),
        "status_codes": dict(Counter(str(item.get("status")) for item in rows if item.get("status") is not None)),
        "trace_ids": [item.get("trace_id") for item in rows if item.get("trace_id")][-20:],
    }


if __name__ == "__main__":
    logger.info("Log MCP 使用本地真实日志: %s", LOG_PATH)
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8003, path="/mcp")