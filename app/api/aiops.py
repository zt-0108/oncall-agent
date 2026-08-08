"""AIOps 数据源状态与流式诊断接口。"""

import asyncio
import json
from uuid import uuid4

from fastapi import APIRouter
from loguru import logger
from sse_starlette.sse import EventSourceResponse

from app.models.aiops import AIOpsRequest
from app.observability.metrics import record_aiops_diagnosis
from app.services.aiops_service import aiops_service
from app.services.source_status_service import source_status_service

router = APIRouter()


@router.get("/aiops/sources")
async def get_aiops_sources() -> dict:
    """返回前端可直接展示的数据源与诊断模式状态。"""
    return await source_status_service.get_status()


@router.post("/aiops")
async def diagnose_stream(request: AIOpsRequest) -> EventSourceResponse:
    """按模式执行 AIOps 诊断，并通过 SSE 返回进度。"""
    logger.info("[会话 {}] 收到 {} AIOps 诊断请求", request.session_id, request.mode)

    async def event_generator():
        metric_recorded = False

        def mark_result(status: str) -> None:
            nonlocal metric_recorded
            if not metric_recorded:
                record_aiops_diagnosis(request.mode, status)
                metric_recorded = True

        try:
            status = await source_status_service.get_status()
            yield _sse({"type": "source_status", "stage": "preflight", **status})

            mode_status = status["modes"][request.mode]
            if not mode_status["available"]:
                mark_result("blocked")
                yield _sse(
                    {
                        "type": "blocked",
                        "stage": "preflight",
                        "message": mode_status["reason"],
                    }
                )
                yield _sse(
                    {
                        "type": "complete",
                        "stage": "blocked",
                        "message": "诊断未启动",
                        "diagnosis": {"status": "blocked", "report": ""},
                    }
                )
                return

            if request.mode == "manual":
                events = aiops_service.diagnose_manual(
                    description=request.description or "",
                    service_name=request.service_name,
                    session_id=request.session_id,
                )
            else:
                events = aiops_service.diagnose_realtime(request.session_id)

            async for event in events:
                event_type = event.get("type")
                if event_type == "complete":
                    mark_result(str(event.get("diagnosis", {}).get("status", "completed")))
                elif event_type == "error":
                    mark_result("error")
                yield _sse(event)
                if event_type in {"complete", "error"}:
                    break
            mark_result("incomplete")
        except asyncio.CancelledError:
            mark_result("cancelled")
            raise
        except Exception:
            mark_result("error")
            error_id = uuid4().hex[:12]
            logger.exception("[会话 {}] AIOps 流异常，错误编号 {}", request.session_id, error_id)
            yield _sse(
                {
                    "type": "error",
                    "stage": "exception",
                    "message": f"诊断请求失败，错误编号：{error_id}",
                }
            )

    return EventSourceResponse(event_generator())


def _sse(payload: dict) -> dict[str, str]:
    return {
        "event": "message",
        "data": json.dumps(payload, ensure_ascii=False),
    }
