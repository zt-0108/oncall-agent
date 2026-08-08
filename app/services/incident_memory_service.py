"""故障记忆持久化：SQLite 保存审计记录，Milvus 提供语义检索。"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.documents import Document
from loguru import logger

from app.config import config
from app.models.incident_memory import (
    IncidentMemory,
    IncidentMemoryCreate,
    IncidentMemorySearchResult,
)
from app.services.vector_store_manager import VectorStoreManager, vector_store_manager


class IncidentMemoryService:
    def __init__(
        self,
        db_path: str | Path | None = None,
        vector_manager: VectorStoreManager | None = None,
    ) -> None:
        self.db_path = Path(db_path or config.incident_memory_db_path)
        self.vector_manager = vector_manager or vector_store_manager
        self._lock = threading.RLock()
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS incident_memories (
                    incident_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    service_name TEXT NOT NULL,
                    symptoms_json TEXT NOT NULL,
                    alerts_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    confirmed_root_cause TEXT NOT NULL,
                    resolution TEXT NOT NULL,
                    verification TEXT NOT NULL,
                    diagnosis_report TEXT NOT NULL,
                    source TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL,
                    vector_source TEXT NOT NULL UNIQUE
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_incident_confirmed_at "
                "ON incident_memories(confirmed_at DESC)"
            )

    @staticmethod
    def _incident_id(now: datetime) -> str:
        return f"INC-{now:%Y%m%d}-{uuid4().hex[:8].upper()}"

    @staticmethod
    def _vector_content(incident_id: str, payload: IncidentMemoryCreate) -> str:
        symptoms = "\n".join(f"- {item}" for item in payload.symptoms)
        alerts = "、".join(payload.alerts) if payload.alerts else "无明确告警名称"
        evidence = "\n".join(f"- {item}" for item in payload.key_evidence)
        return (
            "# 已确认的历史故障案例\n\n"
            "> 边界：这是历史相似案例，只能作为排查线索，不能证明当前故障具有相同根因。"
            "必须使用当前告警、指标和日志重新验证。\n\n"
            f"案例编号：{incident_id}\n"
            f"标题：{payload.title}\n"
            f"服务：{payload.service_name}\n"
            f"来源：{payload.source}\n\n"
            f"## 历史现象\n{symptoms}\n\n"
            f"## 历史告警\n{alerts}\n\n"
            f"## 已确认关键证据\n{evidence}\n\n"
            f"## 已确认根因\n{payload.confirmed_root_cause}\n\n"
            f"## 已验证处置\n{payload.resolution}\n\n"
            f"## 恢复验证\n{payload.verification}"
        )[:7900]

    def confirm_and_store(self, payload: IncidentMemoryCreate) -> IncidentMemory:
        now = datetime.now(UTC)
        incident_id = self._incident_id(now)
        vector_source = f"incident-memory/{incident_id}"
        metadata = {
            "_source": vector_source,
            "_file_name": f"历史故障案例 {incident_id}",
            "_memory_type": "incident_memory",
            "incident_id": incident_id,
            "service_name": payload.service_name,
            "source_kind": payload.source,
            "confirmed": True,
            "confirmed_at": now.isoformat(),
        }
        document = Document(
            page_content=self._vector_content(incident_id, payload),
            metadata=metadata,
        )

        with self._lock:
            self.vector_manager.replace_documents_for_source(vector_source, [document])
            try:
                with self._connection() as connection:
                    connection.execute(
                        """
                        INSERT INTO incident_memories VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        (
                            incident_id,
                            payload.session_id,
                            payload.title,
                            payload.service_name,
                            json.dumps(payload.symptoms, ensure_ascii=False),
                            json.dumps(payload.alerts, ensure_ascii=False),
                            json.dumps(payload.key_evidence, ensure_ascii=False),
                            payload.confirmed_root_cause,
                            payload.resolution,
                            payload.verification,
                            payload.diagnosis_report,
                            payload.source,
                            payload.reviewer,
                            now.isoformat(),
                            vector_source,
                        ),
                    )
            except Exception:
                try:
                    self.vector_manager.delete_by_source(vector_source)
                except Exception:
                    logger.exception("故障记忆数据库写入失败后，向量回滚也失败: {}", incident_id)
                raise

        logger.info("故障记忆已确认并入库: {}", incident_id)
        return self.get(incident_id)

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> IncidentMemory:
        return IncidentMemory(
            incident_id=row["incident_id"],
            session_id=row["session_id"],
            title=row["title"],
            service_name=row["service_name"],
            symptoms=json.loads(row["symptoms_json"]),
            alerts=json.loads(row["alerts_json"]),
            key_evidence=json.loads(row["evidence_json"]),
            confirmed_root_cause=row["confirmed_root_cause"],
            resolution=row["resolution"],
            verification=row["verification"],
            diagnosis_report=row["diagnosis_report"],
            source=row["source"],
            reviewer=row["reviewer"],
            confirmed_at=datetime.fromisoformat(row["confirmed_at"]),
            vector_source=row["vector_source"],
        )

    def get(self, incident_id: str) -> IncidentMemory:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM incident_memories WHERE incident_id = ?", (incident_id,)
            ).fetchone()
        if row is None:
            raise KeyError(incident_id)
        return self._row_to_memory(row)

    def list_recent(self, limit: int = 20) -> list[IncidentMemory]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM incident_memories ORDER BY confirmed_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def search_similar(
        self, query: str, limit: int | None = None
    ) -> list[IncidentMemorySearchResult]:
        top_k = limit or config.incident_memory_top_k
        vector_store = self.vector_manager.get_vector_store()
        pairs = vector_store.similarity_search_with_score(
            query,
            k=top_k,
            expr='metadata["_memory_type"] == "incident_memory"',
        )
        results: list[IncidentMemorySearchResult] = []
        for document, score in pairs:
            metadata: dict[str, Any] = document.metadata
            incident_id = str(metadata.get("incident_id", ""))
            if not incident_id:
                continue
            try:
                memory = self.get(incident_id)
            except KeyError:
                logger.warning("Milvus 中存在无 SQLite 记录的故障记忆: {}", incident_id)
                continue
            results.append(
                IncidentMemorySearchResult(
                    incident_id=memory.incident_id,
                    title=memory.title,
                    service_name=memory.service_name,
                    confirmed_root_cause=memory.confirmed_root_cause,
                    resolution=memory.resolution,
                    source=memory.source,
                    confirmed_at=memory.confirmed_at.isoformat(),
                    score=float(score),
                )
            )
        return results

    def delete(self, incident_id: str) -> None:
        memory = self.get(incident_id)
        with self._lock:
            self.vector_manager.delete_by_source(memory.vector_source)
            with self._connection() as connection:
                connection.execute(
                    "DELETE FROM incident_memories WHERE incident_id = ?", (incident_id,)
                )
        logger.info("故障记忆已删除: {}", incident_id)


incident_memory_service = IncidentMemoryService()
