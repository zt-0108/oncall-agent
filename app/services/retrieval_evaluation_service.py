"""Offline-friendly retrieval quality metrics for fixed evaluation cases."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from langchain_core.documents import Document

Retriever = Callable[[str, int], list[Document]]


@dataclass(frozen=True)
class RetrievalEvalCase:
    id: str
    query: str
    expected_sources: tuple[str, ...]
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetrievalCaseResult:
    id: str
    query: str
    expected_sources: tuple[str, ...]
    retrieved_sources: tuple[str, ...]
    hit_at_3: float
    recall_at_8: float
    reciprocal_rank_at_8: float
    ndcg_at_3: float
    latency_ms: float
    rewrite_applied: bool


@dataclass(frozen=True)
class RetrievalEvaluationSummary:
    case_count: int
    hit_rate_at_3: float
    recall_at_8: float
    mrr_at_8: float
    ndcg_at_3: float
    average_latency_ms: float
    p95_latency_ms: float
    rewrite_rate: float
    cases: tuple[RetrievalCaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _source_name(document: Document) -> str:
    raw_source = document.metadata.get("_file_name") or document.metadata.get("_source") or ""
    return str(raw_source).replace("\\", "/").rsplit("/", maxsplit=1)[-1]


def _ndcg(relevance: Sequence[int], expected_count: int, k: int) -> float:
    dcg = sum(value / math.log2(rank + 1) for rank, value in enumerate(relevance[:k], start=1))
    ideal_count = min(expected_count, k)
    if ideal_count <= 0:
        return 0.0
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return dcg / ideal_dcg


def load_eval_cases(path: str | Path) -> list[RetrievalEvalCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        RetrievalEvalCase(
            id=str(item["id"]),
            query=str(item["query"]),
            expected_sources=tuple(str(source) for source in item["expected_sources"]),
            tags=tuple(str(tag) for tag in item.get("tags", [])),
        )
        for item in payload
    ]


def evaluate_retriever(
    retriever: Retriever,
    cases: Sequence[RetrievalEvalCase],
    *,
    final_k: int = 3,
    candidate_k: int = 8,
) -> RetrievalEvaluationSummary:
    """Evaluate a retriever without coupling the metrics to Milvus or an LLM."""
    request_k = max(final_k, candidate_k)
    case_results: list[RetrievalCaseResult] = []

    for case in cases:
        started = time.perf_counter()
        documents = retriever(case.query, request_k)
        latency_ms = (time.perf_counter() - started) * 1000
        sources = tuple(_source_name(document) for document in documents)
        expected = set(case.expected_sources)
        seen_relevant_sources: set[str] = set()
        relevance: list[int] = []
        for source in sources:
            is_new_relevant = source in expected and source not in seen_relevant_sources
            relevance.append(int(is_new_relevant))
            if is_new_relevant:
                seen_relevant_sources.add(source)
        first_relevant_rank = next(
            (rank for rank, value in enumerate(relevance[:candidate_k], start=1) if value),
            None,
        )
        retrieved_relevant_sources = set(sources[:candidate_k]) & expected
        rewrite_applied = any(
            bool(document.metadata.get("_query_rewrite_applied")) for document in documents
        )

        case_results.append(
            RetrievalCaseResult(
                id=case.id,
                query=case.query,
                expected_sources=case.expected_sources,
                retrieved_sources=sources,
                hit_at_3=float(any(relevance[:final_k])),
                recall_at_8=(len(retrieved_relevant_sources) / len(expected) if expected else 0.0),
                reciprocal_rank_at_8=(
                    1.0 / first_relevant_rank if first_relevant_rank is not None else 0.0
                ),
                ndcg_at_3=_ndcg(relevance, len(expected), final_k),
                latency_ms=round(latency_ms, 3),
                rewrite_applied=rewrite_applied,
            )
        )

    if not case_results:
        return RetrievalEvaluationSummary(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, ())

    sorted_latencies = sorted(item.latency_ms for item in case_results)
    p95_index = max(0, math.ceil(len(sorted_latencies) * 0.95) - 1)
    return RetrievalEvaluationSummary(
        case_count=len(case_results),
        hit_rate_at_3=mean(item.hit_at_3 for item in case_results),
        recall_at_8=mean(item.recall_at_8 for item in case_results),
        mrr_at_8=mean(item.reciprocal_rank_at_8 for item in case_results),
        ndcg_at_3=mean(item.ndcg_at_3 for item in case_results),
        average_latency_ms=mean(item.latency_ms for item in case_results),
        p95_latency_ms=sorted_latencies[p95_index],
        rewrite_rate=mean(float(item.rewrite_applied) for item in case_results),
        cases=tuple(case_results),
    )
