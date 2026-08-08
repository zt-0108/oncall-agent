"""RAG Agent 对话接口。"""

import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from loguru import logger
from sse_starlette.sse import EventSourceResponse

from app.agent.mcp_client import format_exception_chain
from app.models.request import ChatRequest, ClearRequest
from app.models.response import ApiResponse, SessionInfoResponse
from app.services.rag_agent_service import rag_agent_service

router = APIRouter()


@router.post("/chat")
async def chat(request: ChatRequest):
    """非流式对话接口。"""
    try:
        answer = await rag_agent_service.query(request.question, session_id=request.id)
        return {
            "code": 200,
            "message": "success",
            "data": {
                "success": True,
                "answer": answer,
                "errorMessage": None,
            },
        }
    except Exception as exc:
        error_id = uuid4().hex
        logger.error(
            "[{}] 对话接口失败，session={}\n{}",
            error_id,
            request.id,
            format_exception_chain(exc),
        )
        raise HTTPException(
            status_code=500,
            detail={"message": "对话处理失败", "error_id": error_id},
        ) from exc


@router.post("/chat_stream")
async def chat_stream(request: ChatRequest):
    """SSE 流式对话接口。"""

    async def event_generator():
        try:
            async for chunk in rag_agent_service.query_stream(
                request.question,
                session_id=request.id,
            ):
                chunk_type = chunk.get("type", "unknown")
                chunk_data = chunk.get("data")

                if chunk_type == "debug":
                    payload = {
                        "type": "debug",
                        "node": chunk.get("node", "unknown"),
                        "message_type": chunk.get("message_type", "unknown"),
                    }
                elif chunk_type in {"tool_call", "search_results", "content"}:
                    payload = {"type": chunk_type, "data": chunk_data}
                elif chunk_type == "complete":
                    payload = {"type": "done", "data": chunk_data}
                elif chunk_type == "error":
                    payload = {"type": "error", "data": chunk_data}
                else:
                    continue

                yield {
                    "event": "message",
                    "data": json.dumps(payload, ensure_ascii=False),
                }
        except Exception as exc:
            error_id = uuid4().hex
            logger.error(
                "[{}] 流式对话失败，session={}\n{}",
                error_id,
                request.id,
                format_exception_chain(exc),
            )
            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "type": "error",
                        "data": {
                            "message": "流式对话处理失败",
                            "error_id": error_id,
                        },
                    },
                    ensure_ascii=False,
                ),
            }

    return EventSourceResponse(event_generator())


@router.post("/chat/clear", response_model=ApiResponse)
async def clear_session(request: ClearRequest) -> ApiResponse:
    try:
        success = rag_agent_service.clear_session(request.session_id)
        if not success:
            raise RuntimeError("checkpointer delete failed")
        return ApiResponse(status="success", message="会话已清空", data=None)
    except Exception as exc:
        error_id = uuid4().hex
        logger.exception("[{}] 清空会话失败", error_id)
        raise HTTPException(
            status_code=500,
            detail={"message": "清空会话失败", "error_id": error_id},
        ) from exc


@router.get("/chat/session/{session_id}", response_model=SessionInfoResponse)
async def get_session_info(session_id: str) -> SessionInfoResponse:
    try:
        history = rag_agent_service.get_session_history(session_id)
        return SessionInfoResponse(
            session_id=session_id,
            message_count=len(history),
            history=history,
        )
    except Exception as exc:
        error_id = uuid4().hex
        logger.exception("[{}] 获取会话信息失败", error_id)
        raise HTTPException(
            status_code=500,
            detail={"message": "获取会话信息失败", "error_id": error_id},
        ) from exc
