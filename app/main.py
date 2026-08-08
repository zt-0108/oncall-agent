"""FastAPI 应用入口。"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from loguru import logger
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api import aiops, chat, fault_lab, file, health, incident_memory
from app.config import config
from app.core.milvus_client import milvus_manager
from app.observability.metrics import MetricsMiddleware
from app.services.vector_store_manager import vector_store_manager

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    """初始化外部资源；失败时保留健康检查和降级响应能力。"""
    logger.info(
        "{} v{} 启动，监听 {}:{}", config.app_name, config.app_version, config.host, config.port
    )

    try:
        await asyncio.to_thread(milvus_manager.connect)
        await asyncio.to_thread(vector_store_manager.initialize)
        logger.info("Milvus 与 VectorStore 初始化成功")
    except Exception:
        logger.exception("Milvus 初始化失败，应用将以降级模式启动")

    try:
        yield
    finally:
        vector_store_manager.close()
        await asyncio.to_thread(milvus_manager.close)
        logger.info("{} 已关闭", config.app_name)


app = FastAPI(
    title=config.app_name,
    version=config.app_version,
    description="基于 LangChain 和 LangGraph 的智能 On-call 运维系统",
    lifespan=lifespan,
)

app.add_middleware(MetricsMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origin_list,
    allow_credentials=config.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["健康检查"])
app.include_router(chat.router, prefix="/api", tags=["对话"])
app.include_router(file.router, prefix="/api", tags=["文件管理"])
app.include_router(fault_lab.router, prefix="/api", tags=["Fault Lab"])
app.include_router(aiops.router, prefix="/api", tags=["AIOps 智能运维"])
app.include_router(incident_memory.router, prefix="/api", tags=["故障记忆"])

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """暴露 Prometheus 文本格式的应用运行指标。"""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/")
async def root():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {
        "message": f"Welcome to {config.app_name} API",
        "version": config.app_version,
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.host,
        port=config.port,
        reload=config.debug,
        log_level="info",
    )
