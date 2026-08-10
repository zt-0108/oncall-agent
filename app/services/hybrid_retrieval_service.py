"""Hybrid knowledge retrieval with dense, BM25, metadata, and RRF fusion."""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from hashlib import sha256
from typing import Any

from langchain_core.documents import Document
from loguru import logger

from app.config import config
from app.core.milvus_client import milvus_manager
from app.services.vector_store_manager import vector_store_manager

KNOWLEDGE_EXTENSIONS = {".md", ".txt"}
KNOWLEDGE_FILTER_EXPR = 'metadata["_extension"] in [".md", ".txt"]'
ASCII_OR_CJK_PATTERN = re.compile(r"[a-z0-9_.:/-]+|[\u4e00-\u9fff]+", re.IGNORECASE)

DenseSearch = Callable[[str, int], list[Document]]
CorpusLoader = Callable[[int], list[Document]]


def tokenize_for_bm25(text: str) -> list[str]:
    """Tokenize English identifiers and Chinese text without external dependencies."""
    tokens: list[str] = []
    for match in ASCII_OR_CJK_PATTERN.findall(text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", match):
            tokens.append(match)
            if len(match) > 1:
                tokens.extend(match[index : index + 2] for index in range(len(match) - 1))
        else:
            tokens.append(match)
    return tokens


def document_identity(document: Document) -> str:
    """Return a stable identity for deduplication across retrieval routes."""
    if document.id:
        return str(document.id)
    source = str(document.metadata.get("_source", ""))
    chunk = str(document.metadata.get("chunk_index", ""))
    payload = f"{source}\n{chunk}\n{document.page_content}".encode()
    return sha256(payload).hexdigest()


def bm25_rank(
    query: str,
    documents: Sequence[Document],
    limit: int,
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[Document]:
    """Rank documents with a small in-process BM25 implementation."""
    if not documents or limit <= 0:
        return []
    query_tokens = tokenize_for_bm25(query)
    if not query_tokens:
        return []

    tokenized_documents = [tokenize_for_bm25(document.page_content) for document in documents]
    average_length = sum(len(tokens) for tokens in tokenized_documents) / len(documents)
    if average_length <= 0:
        return []

    document_frequency: Counter[str] = Counter()
    for tokens in tokenized_documents:
        document_frequency.update(set(tokens))

    document_count = len(documents)
    unique_query_tokens = set(query_tokens)
    scored: list[tuple[float, str, Document]] = []
    for document, tokens in zip(documents, tokenized_documents, strict=True):
        if not tokens:
            continue
        term_frequency = Counter(tokens)
        length_normalization = k1 * (1 - b + b * len(tokens) / average_length)
        score = 0.0
        for token in unique_query_tokens:
            frequency = term_frequency.get(token, 0)
            if frequency <= 0:
                continue
            frequency_in_documents = document_frequency[token]
            inverse_document_frequency = math.log(
                1 + (document_count - frequency_in_documents + 0.5) / (frequency_in_documents + 0.5)
            )
            score += inverse_document_frequency * (
                frequency * (k1 + 1) / (frequency + length_normalization)
            )
        if score > 0:
            scored.append((score, document_identity(document), document))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [document for _, _, document in scored[:limit]]


def _metadata_values(metadata: dict[str, Any]) -> Iterable[str]:
    for key, value in metadata.items():
        if key.startswith("_retrieval_") or key == "_rrf_score":
            continue
        if isinstance(value, str):
            yield value
        elif isinstance(value, (int, float, bool)):
            yield str(value)
        elif isinstance(value, list):
            yield from (str(item) for item in value if isinstance(item, (str, int, float)))


def metadata_rank(query: str, documents: Sequence[Document], limit: int) -> list[Document]:
    """Rank exact service, alert, filename, and heading matches from metadata."""
    if limit <= 0:
        return []
    query_tokens = set(tokenize_for_bm25(query))
    if not query_tokens:
        return []

    scored: list[tuple[int, str, Document]] = []
    for document in documents:
        metadata_text = " ".join(_metadata_values(document.metadata))
        metadata_tokens = set(tokenize_for_bm25(metadata_text))
        score = len(query_tokens & metadata_tokens)
        if score > 0:
            scored.append((score, document_identity(document), document))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [document for _, _, document in scored[:limit]]


def reciprocal_rank_fusion(
    rankings: dict[str, Sequence[Document]],
    *,
    rrf_k: int,
    top_k: int,
) -> list[Document]:
    """Fuse route rankings using reciprocal rank fusion and deduplicate chunks."""
    if top_k <= 0:
        return []
    fusion_constant = max(1, rrf_k)
    scores: defaultdict[str, float] = defaultdict(float)
    documents_by_id: dict[str, Document] = {}
    routes_by_id: defaultdict[str, list[str]] = defaultdict(list)

    for route_name, documents in rankings.items():
        seen_in_route: set[str] = set()
        for rank, document in enumerate(documents, start=1):
            identity = document_identity(document)
            if identity in seen_in_route:
                continue
            seen_in_route.add(identity)
            documents_by_id.setdefault(identity, document)
            scores[identity] += 1.0 / (fusion_constant + rank)
            routes_by_id[identity].append(route_name)

    ordered_ids = sorted(scores, key=lambda identity: (-scores[identity], identity))[:top_k]
    fused_documents: list[Document] = []
    for identity in ordered_ids:
        document = documents_by_id[identity]
        metadata = dict(document.metadata)
        metadata["_retrieval_routes"] = routes_by_id[identity]
        metadata["_rrf_score"] = round(scores[identity], 8)
        fused_documents.append(
            Document(id=document.id, page_content=document.page_content, metadata=metadata)
        )
    return fused_documents


class HybridRetrievalService:
    """Combine dense, keyword, and metadata rankings with graceful route degradation."""

    def __init__(
        self,
        dense_search: DenseSearch | None = None,
        corpus_loader: CorpusLoader | None = None,
    ) -> None:
        self._dense_search = dense_search or self._search_dense
        self._corpus_loader = corpus_loader or self._load_knowledge_corpus

    @staticmethod
    def _search_dense(query: str, limit: int) -> list[Document]:
        vector_store = vector_store_manager.get_vector_store()
        return list(
            vector_store.similarity_search(
                query,
                k=limit,
                expr=KNOWLEDGE_FILTER_EXPR,
            )
        )

    @staticmethod
    def _load_knowledge_corpus(limit: int) -> list[Document]:
        collection = milvus_manager.get_collection()
        rows = collection.query(
            expr='id != ""',
            output_fields=["id", "content", "metadata"],
            limit=limit,
        )
        documents: list[Document] = []
        for row in rows:
            metadata = row.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            if metadata.get("_extension") not in KNOWLEDGE_EXTENSIONS:
                continue
            content = str(row.get("content") or "")
            if not content.strip():
                continue
            documents.append(
                Document(
                    id=str(row.get("id") or "") or None,
                    page_content=content,
                    metadata=metadata,
                )
            )
        return documents

    def retrieve(self, query: str, top_k: int | None = None) -> list[Document]:
        """Run available routes independently and fuse successful rankings."""
        final_k = max(1, top_k or config.rag_top_k)
        candidate_k = max(final_k, config.rag_candidate_k)
        rankings: dict[str, Sequence[Document]] = {}

        try:
            rankings["dense"] = self._dense_search(query, candidate_k)
        except Exception:
            logger.exception("Dense 向量召回失败，继续使用其他召回链路")

        corpus: list[Document] = []
        try:
            corpus = self._corpus_loader(max(candidate_k, config.rag_keyword_corpus_limit))
        except Exception:
            logger.exception("关键词语料加载失败，跳过 BM25 与 Metadata 召回")

        if corpus:
            rankings["bm25"] = bm25_rank(query, corpus, candidate_k)
            rankings["metadata"] = metadata_rank(query, corpus, candidate_k)

        fused = reciprocal_rank_fusion(
            rankings,
            rrf_k=config.rag_rrf_k,
            top_k=final_k,
        )
        logger.info(
            "混合检索完成: query={!r}, routes={}, candidates={}, final={}",
            query,
            {name: len(documents) for name, documents in rankings.items()},
            sum(len(documents) for documents in rankings.values()),
            len(fused),
        )
        return fused


hybrid_retrieval_service = HybridRetrievalService()
