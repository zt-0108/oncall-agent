"""文件上传与目录索引接口。"""

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import aiofiles  # type: ignore[import-untyped]
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from loguru import logger

from app.services.vector_index_service import vector_index_service

router = APIRouter()

UPLOAD_DIR = Path("./uploads")
ALLOWED_EXTENSIONS = {"txt", "md"}
MAX_FILE_SIZE = 10 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 1024 * 1024


@router.post("/upload")
async def upload_file(file: Annotated[UploadFile, File(...)], force: bool = False):
    """上传文本文件并创建向量索引。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    safe_filename = _sanitize_filename(file.filename)
    file_extension = _get_file_extension(safe_filename)
    if file_extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式，仅支持: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_path = UPLOAD_DIR / safe_filename
    temp_path = UPLOAD_DIR / f".{uuid4().hex}.upload"
    file_size = 0
    unchanged = False

    try:
        async with aiofiles.open(temp_path, "wb") as output:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                file_size += len(chunk)
                if file_size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=f"文件大小超过限制（最大 {MAX_FILE_SIZE} 字节）",
                    )
                await output.write(chunk)

        unchanged = (
            not force and file_path.is_file() and _sha256_file(temp_path) == _sha256_file(file_path)
        )
        if not unchanged:
            # 同一文件系统中的原子替换，避免校验前删除旧文件。
            temp_path.replace(file_path)
    except HTTPException:
        raise
    except OSError as exc:
        logger.exception("保存上传文件失败: {}", file_path)
        raise HTTPException(status_code=500, detail="保存上传文件失败") from exc
    finally:
        await file.close()
        temp_path.unlink(missing_ok=True)

    if unchanged:
        return JSONResponse(
            status_code=200,
            content={
                "code": 200,
                "message": "unchanged",
                "data": {
                    "filename": safe_filename,
                    "file_path": str(file_path),
                    "size": file_size,
                    "indexed": False,
                },
            },
        )

    try:
        await asyncio.to_thread(vector_index_service.index_single_file, str(file_path))
    except Exception as exc:
        logger.exception("向量索引创建失败: {}", file_path)
        raise HTTPException(
            status_code=500,
            detail="文件已保存，但向量索引创建失败，请稍后重试索引",
        ) from exc

    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "message": "success",
            "data": {
                "filename": safe_filename,
                "file_path": str(file_path),
                "size": file_size,
                "indexed": True,
            },
        },
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(UPLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


@router.post("/index_directory")
async def index_directory(directory_path: str | None = None):
    """索引 uploads 目录或其子目录中的所有支持文件。"""
    allowed_directory = _resolve_upload_directory(directory_path)
    logger.info("开始索引目录: {}", allowed_directory)

    result = await asyncio.to_thread(
        vector_index_service.index_directory,
        str(allowed_directory),
    )
    return JSONResponse(
        status_code=200,
        content={
            "code": 200,
            "message": "success" if result.success else "partial_success",
            "data": result.to_dict(),
        },
    )


def _get_file_extension(filename: str) -> str:
    parts = filename.rsplit(".", 1)
    return parts[1].lower() if len(parts) == 2 else ""


def _sanitize_filename(filename: str) -> str:
    """移除路径信息和不安全字符，只返回文件名。"""
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    sanitized = re.sub(r"[^\w.\-]", "_", basename, flags=re.UNICODE).strip(" .")
    if not sanitized or sanitized in {".", ".."}:
        raise HTTPException(status_code=400, detail="文件名无效")
    return sanitized


def _resolve_upload_directory(directory_path: str | None) -> Path:
    """解析索引目录，并确保它位于 uploads 根目录内。"""
    upload_root = UPLOAD_DIR.resolve()
    candidate = Path(directory_path).resolve() if directory_path else upload_root
    try:
        candidate.relative_to(upload_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="只能索引 uploads 目录及其子目录") from exc

    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail="索引目录不存在")
    return candidate
