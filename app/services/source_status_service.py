"""AIOps 外部数据源预检。"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from loguru import logger

from app.agent.mcp_client import load_mcp_tools_independently
from app.config import config
from app.core.milvus_client import milvus_manager
from app.tools.query_metrics_alerts import query_prometheus_alerts_api


class SourceStatusService:
    async def _probe_prometheus(self) -> dict[str, Any]:
        try:
            body, error = await asyncio.wait_for(
                asyncio.to_thread(query_prometheus_alerts_api), timeout=4.0
            )
        except TimeoutError:
            return self._source(False, "连接超时", endpoint=config.prometheus_base_url)
        if error or body.get("status") != "success":
            return self._source(False, "无法连接", endpoint=config.prometheus_base_url)
        alerts = (body.get("data") or {}).get("alerts") or []
        return self._source(
            True,
            f"已连接，当前 {len(alerts)} 条活动告警",
            endpoint=config.prometheus_base_url,
            alert_count=len(alerts),
            data_origin="live_local",
        )

    async def _probe_mcp(self) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            _, errors, counts = await asyncio.wait_for(load_mcp_tools_independently(), timeout=6.0)
        except TimeoutError:
            errors = dict.fromkeys(config.mcp_servers, "timeout")
            counts = dict.fromkeys(config.mcp_servers, 0)
        except Exception:
            logger.exception("MCP 数据源预检异常")
            errors = dict.fromkeys(config.mcp_servers, "unavailable")
            counts = dict.fromkeys(config.mcp_servers, 0)

        result: dict[str, dict[str, Any]] = {}
        for name, server in config.mcp_servers.items():
            count = counts.get(name, 0)
            available = name not in errors
            backend = config.log_data_backend if name == "cls" else "prometheus"
            result[name] = self._source(
                available,
                f"已连接，发现 {count} 个真实查询工具" if available else "无法连接",
                endpoint=str(server.get("url", "")),
                tool_count=count,
                backend=backend,
                data_origin="live_local",
            )
        return result.get("cls", self._source(False, "未配置")), result.get(
            "monitor", self._source(False, "未配置")
        )

    async def _probe_milvus(self) -> dict[str, Any]:
        try:
            available = await asyncio.wait_for(
                asyncio.to_thread(milvus_manager.health_check), timeout=4.0
            )
        except Exception:
            available = False
        return self._source(
            bool(available),
            "向量知识库可用" if available else "向量知识库不可用",
            endpoint=f"{config.milvus_host}:{config.milvus_port}",
            data_origin="live_local",
        )

    async def _probe_fault_lab(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
                response = await client.get(f"{config.fault_lab_base_url}/health")
                response.raise_for_status()
                state = response.json()
        except (httpx.HTTPError, ValueError):
            return self._source(
                False,
                "实验服务不可用",
                endpoint=config.fault_lab_base_url,
                data_origin="live_local",
            )
        active = (state.get("active_scenario") or {}).get("title", "未知")
        return self._source(
            True,
            f"运行中，当前场景：{active}",
            endpoint=config.fault_lab_base_url,
            active_scenario=(state.get("active_scenario") or {}).get("id"),
            data_origin="live_local",
        )

    async def get_status(self) -> dict[str, Any]:
        prometheus, mcp_pair, milvus, fault_lab = await asyncio.gather(
            self._probe_prometheus(),
            self._probe_mcp(),
            self._probe_milvus(),
            self._probe_fault_lab(),
        )
        logs, monitor = mcp_pair
        sources = {
            "prometheus": prometheus,
            "cls": logs,
            "monitor": monitor,
            "milvus": milvus,
            "fault_lab": fault_lab,
        }
        return {
            "overall": "ready"
            if all(item["available"] for item in sources.values())
            else "degraded",
            "sources": sources,
            "modes": {
                "realtime": {
                    "available": prometheus["available"],
                    "reason": ""
                    if prometheus["available"]
                    else "实时诊断需要可用的 Prometheus 告警源",
                },
                "manual": {
                    "available": True,
                    "reason": "可根据人工输入的故障现象进行诊断",
                },
            },
        }

    @staticmethod
    def _source(available: bool, message: str, **extra: Any) -> dict[str, Any]:
        return {"available": available, "message": message, **extra}


source_status_service = SourceStatusService()
