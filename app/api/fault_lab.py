"""Fault Lab 控制代理，避免前端跨端口并统一错误处理。"""

from __future__ import annotations

import asyncio
import secrets
from collections import Counter
from typing import Any, cast
from uuid import uuid4

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import config

router = APIRouter()


class TrafficRunRequest(BaseModel):
    requests: int = Field(default=12, ge=1, le=30)
    concurrency: int = Field(default=4, ge=1, le=10)


def _ensure_enabled() -> None:
    if not config.fault_lab_enabled:
        raise HTTPException(status_code=404, detail="Fault Lab 未启用")


def _authorize_control(x_fault_lab_token: str | None, client_host: str) -> None:
    """保护会改变实验状态的接口；本机模式允许无令牌使用。"""
    _ensure_enabled()
    expected = config.fault_lab_control_token
    if expected is not None:
        if not x_fault_lab_token or not secrets.compare_digest(
            x_fault_lab_token, expected.get_secret_value()
        ):
            raise HTTPException(status_code=403, detail="Fault Lab 控制令牌无效")
        return
    if client_host not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(
            status_code=403,
            detail="非本机请求必须配置 FAULT_LAB_CONTROL_TOKEN",
        )


async def _request(method: str, path: str) -> dict[str, Any]:
    _ensure_enabled()
    try:
        async with httpx.AsyncClient(
            base_url=config.fault_lab_base_url,
            timeout=config.fault_lab_request_timeout,
            trust_env=False,
        ) as client:
            response = await client.request(method, path)
            response.raise_for_status()
            return cast(dict[str, Any], response.json())
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Fault Lab 服务不可用") from exc


@router.get("/fault-lab")
async def overview() -> dict[str, Any]:
    scenarios, state = await asyncio.gather(
        _request("GET", "/api/scenarios"),
        _request("GET", "/api/state"),
    )
    return {"scenarios": scenarios["scenarios"], "state": state, "data_origin": "live_local"}


@router.post("/fault-lab/scenarios/{scenario_id}/activate")
async def activate(
    scenario_id: str,
    request: Request,
    x_fault_lab_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize_control(x_fault_lab_token, request.client.host if request.client else "")
    return await _request("POST", f"/api/scenarios/{scenario_id}/activate")


@router.post("/fault-lab/reset")
async def reset(
    request: Request,
    x_fault_lab_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize_control(x_fault_lab_token, request.client.host if request.client else "")
    return await _request("POST", "/api/reset")


@router.post("/fault-lab/run")
async def run_traffic(
    traffic: TrafficRunRequest,
    request: Request,
    x_fault_lab_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize_control(x_fault_lab_token, request.client.host if request.client else "")
    semaphore = asyncio.Semaphore(traffic.concurrency)

    async def run_one() -> dict[str, Any]:
        async with semaphore:
            trace_id = uuid4().hex
            try:
                async with httpx.AsyncClient(
                    base_url=config.fault_lab_base_url,
                    timeout=config.fault_lab_request_timeout,
                    trust_env=False,
                ) as client:
                    response = await client.post("/api/checkout", headers={"x-trace-id": trace_id})
                    try:
                        body = response.json()
                    except ValueError:
                        body = {"trace_id": trace_id}
                    return {"status": response.status_code, **body}
            except httpx.HTTPError:
                return {"status": 0, "trace_id": trace_id, "message": "请求失败"}

    results = await asyncio.gather(*(run_one() for _ in range(traffic.requests)))
    statuses = Counter(str(item["status"]) for item in results)
    return {
        "requested": traffic.requests,
        "completed": len(results),
        "status_counts": dict(statuses),
        "trace_ids": [item.get("trace_id") for item in results if item.get("trace_id")],
        "data_origin": "live_local",
    }
