"""向量存储管理器：封装 Milvus VectorStore 操作。"""

import json
import threading
import time
import uuid

from langchain_core.documents import Document
from langchain_milvus import Milvus
from loguru import logger

from app.config import config
from app.core.milvus_client import milvus_manager
from app.services.vector_embedding_service import vector_embedding_service

COLLECTION_NAME = "biz"


class VectorStoreManager:
    """延迟初始化并集中管理向量存储。"""

    def __init__(self) -> None:
        self.vector_store: Milvus | None = None
        self.collection_name = COLLECTION_NAME
        self._initialize_lock = threading.RLock()

    def initialize(self) -> Milvus:
        """初始化向量存储；重复调用是安全的。"""
        if self.vector_store is not None:
            return self.vector_store

        with self._initialize_lock:
            if self.vector_store is not None:
                return self.vector_store

            milvus_manager.connect()
            connection_args = {
                "host": config.milvus_host,
                "port": config.milvus_port,
            }
            self.vector_store = Milvus(
                embedding_function=vector_embedding_service,
                collection_name=self.collection_name,
                connection_args=connection_args,
                auto_id=False,
                drop_old=False,
                text_field="content",
                vector_field="vector",
                primary_field="id",
                metadata_field="metadata",
            )
            logger.info(
                "VectorStore 初始化成功: {}:{}, collection={}",
                config.milvus_host,
                config.milvus_port,
                self.collection_name,
            )
            return self.vector_store

    def close(self) -> None:
        """丢弃本地 VectorStore 引用；连接由 MilvusClientManager 统一关闭。"""
        self.vector_store = None

    def add_documents(self, documents: list[Document]) -> list[str]:
        if not documents:
            return []

        vector_store = self.initialize()
        start_time = time.monotonic()
        ids = [str(uuid.uuid4()) for _ in documents]
        result_ids = vector_store.add_documents(documents, ids=ids)
        milvus_manager.get_collection().flush()
        elapsed = time.monotonic() - start_time
        logger.info(
            "批量添加 {} 个文档完成，耗时 {:.2f}s，平均 {:.2f}s/个",
            len(documents),
            elapsed,
            elapsed / len(documents),
        )
        return list(result_ids)

    @staticmethod
    def _source_expression(file_path: str) -> str:
        # json.dumps 负责转义引号和反斜杠，避免表达式注入或语法损坏。
        return f'metadata["_source"] == {json.dumps(file_path, ensure_ascii=False)}'

    def _find_ids_by_source(self, file_path: str) -> list[str]:
        collection = milvus_manager.get_collection()
        rows = collection.query(
            expr=self._source_expression(file_path),
            output_fields=["id"],
        )
        return [str(row["id"]) for row in rows if row.get("id") is not None]

    def _delete_ids(self, ids: list[str]) -> int:
        if not ids:
            return 0
        collection = milvus_manager.get_collection()
        result = collection.delete(f"id in {json.dumps(ids, ensure_ascii=False)}")
        collection.flush()
        return int(getattr(result, "delete_count", 0) or 0)

    def replace_documents_for_source(
        self,
        file_path: str,
        documents: list[Document],
    ) -> list[str]:
        """先写入新版本，再删除旧版本；写入失败时保留旧索引。"""
        self.initialize()
        old_ids = self._find_ids_by_source(file_path)
        new_ids = self.add_documents(documents)

        try:
            deleted_count = self._delete_ids(old_ids)
        except Exception:
            # 删除旧版本失败时回滚本次新增，避免新旧版本长期重复。
            try:
                self._delete_ids(new_ids)
            except Exception:
                logger.exception("回滚新向量失败，source={}", file_path)
            raise

        logger.info(
            "替换文件索引完成: source={}, old={}, new={}",
            file_path,
            deleted_count,
            len(new_ids),
        )
        return new_ids

    def delete_by_source(self, file_path: str) -> int:
        self.initialize()
        ids = self._find_ids_by_source(file_path)
        deleted_count = self._delete_ids(ids)
        logger.info("删除文件索引: source={}, count={}", file_path, deleted_count)
        return deleted_count

    def get_vector_store(self) -> Milvus:
        return self.initialize()

    def similarity_search(self, query: str, k: int = 3) -> list[Document]:
        try:
            docs = self.initialize().similarity_search(query, k=k)
            return list(docs)
        except Exception:
            logger.exception("相似度搜索失败")
            return []


vector_store_manager = VectorStoreManager()
