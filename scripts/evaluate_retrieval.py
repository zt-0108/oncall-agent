"""Compare the current retriever with conditional Query Rewrite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from langchain_core.documents import Document
from loguru import logger

from app.services.document_splitter_service import document_splitter_service
from app.services.hybrid_retrieval_service import HybridRetrievalService
from app.services.query_rewrite_service import QueryRewriteResult
from app.services.retrieval_evaluation_service import (
    RetrievalEvaluationSummary,
    evaluate_retriever,
    load_eval_cases,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def identity_rewriter(query: str) -> QueryRewriteResult:
    stripped = query.strip()
    return QueryRewriteResult(stripped, stripped, False, False, "baseline")


def load_local_corpus() -> list[Document]:
    corpus: list[Document] = []
    for path in sorted((PROJECT_ROOT / "aiops-docs").glob("*.md")):
        documents = document_splitter_service.split_document(
            path.read_text(encoding="utf-8"),
            path.as_posix(),
        )
        for index, document in enumerate(documents):
            document.id = f"{path.name}:{index}"
            document.metadata["chunk_index"] = index
        corpus.extend(documents)
    return corpus


def build_service(*, offline: bool, rewrite: bool) -> HybridRetrievalService:
    query_rewriter = None if rewrite else identity_rewriter
    if not offline:
        return HybridRetrievalService(query_rewriter=query_rewriter)

    corpus = load_local_corpus()
    return HybridRetrievalService(
        dense_search=lambda _query, _limit: [],
        corpus_loader=lambda limit: corpus[:limit],
        query_rewriter=query_rewriter,
    )


def compact_summary(summary: RetrievalEvaluationSummary) -> dict[str, float | int]:
    return {
        "cases": summary.case_count,
        "hit_rate_at_3": round(summary.hit_rate_at_3, 4),
        "recall_at_8": round(summary.recall_at_8, 4),
        "mrr_at_8": round(summary.mrr_at_8, 4),
        "ndcg_at_3": round(summary.ndcg_at_3, 4),
        "average_latency_ms": round(summary.average_latency_ms, 2),
        "p95_latency_ms": round(summary.p95_latency_ms, 2),
        "rewrite_rate": round(summary.rewrite_rate, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=PROJECT_ROOT / "evals" / "retrieval_cases.json",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="只评测本地 BM25 与元数据召回，不连接 Milvus。",
    )
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="只运行不调用 Query Rewrite 的基线。",
    )
    parser.add_argument("--details", action="store_true", help="输出逐条评测结果。")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="ERROR")
    cases = load_eval_cases(args.cases)
    summaries: dict[str, RetrievalEvaluationSummary] = {}
    variants = (
        (("baseline", False),)
        if args.baseline_only
        else (
            ("baseline", False),
            ("query_rewrite", True),
        )
    )
    for name, rewrite in variants:
        service = build_service(offline=args.offline, rewrite=rewrite)
        summaries[name] = evaluate_retriever(service.retrieve, cases)

    output: dict[str, object] = {
        "mode": "offline_lexical" if args.offline else "live_hybrid",
        **{name: compact_summary(summary) for name, summary in summaries.items()},
    }
    if args.details:
        output["details"] = {
            name: [
                {
                    "id": result.id,
                    "expected": result.expected_sources,
                    "retrieved": result.retrieved_sources,
                    "hit_at_3": result.hit_at_3,
                    "recall_at_8": result.recall_at_8,
                    "rewrite_applied": result.rewrite_applied,
                }
                for result in summary.cases
            ]
            for name, summary in summaries.items()
        }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
