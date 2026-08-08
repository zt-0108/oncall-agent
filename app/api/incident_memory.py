"""故障复盘确认与记忆管理接口。"""

import asyncio

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.models.incident_memory import (
    IncidentMemory,
    IncidentMemoryCreate,
    IncidentMemorySearchResult,
)
from app.services.incident_memory_service import incident_memory_service

router = APIRouter()


@router.post("/incidents", response_model=IncidentMemory, status_code=status.HTTP_201_CREATED)
async def confirm_incident(payload: IncidentMemoryCreate) -> IncidentMemory:
    """人工确认后写入审计库和 Milvus；任一写入失败都不会返回成功。"""
    try:
        return await asyncio.to_thread(incident_memory_service.confirm_and_store, payload)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="故障记忆入库失败，请检查 Milvus") from exc


@router.get("/incidents", response_model=list[IncidentMemory])
async def list_incidents(limit: int = Query(default=20, ge=1, le=100)) -> list[IncidentMemory]:
    return await asyncio.to_thread(incident_memory_service.list_recent, limit)


@router.get("/incidents/search", response_model=list[IncidentMemorySearchResult])
async def search_incidents(
    query: str = Query(min_length=2, max_length=1000),
    limit: int = Query(default=3, ge=1, le=10),
) -> list[IncidentMemorySearchResult]:
    try:
        return await asyncio.to_thread(incident_memory_service.search_similar, query, limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="历史故障检索失败") from exc


@router.delete("/incidents/{incident_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_incident(incident_id: str) -> Response:
    try:
        await asyncio.to_thread(incident_memory_service.delete, incident_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="故障记忆不存在") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="故障记忆删除失败") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
