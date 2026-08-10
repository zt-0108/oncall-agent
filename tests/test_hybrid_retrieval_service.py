from langchain_core.documents import Document

import app.services.hybrid_retrieval_service as hybrid_module
from app.services.hybrid_retrieval_service import (
    KNOWLEDGE_FILTER_EXPR,
    HybridRetrievalService,
    bm25_rank,
    metadata_rank,
    reciprocal_rank_fusion,
    tokenize_for_bm25,
)


def make_document(identifier: str, content: str, **metadata: str) -> Document:
    return Document(id=identifier, page_content=content, metadata=metadata)


def test_tokenizer_keeps_identifiers_and_chinese_bigrams():
    tokens = tokenize_for_bm25("checkoutservice 数据库连接失败 DBConnectionError")

    assert "checkoutservice" in tokens
    assert "数据库连接失败" in tokens
    assert "连接" in tokens
    assert "dbconnectionerror" in tokens


def test_bm25_prioritizes_exact_error_and_log_terms():
    exact = make_document(
        "exact",
        "checkoutservice 报错 DBConnectionError，数据库连接被拒绝",
    )
    generic = make_document("generic", "接口延迟升高时检查 CPU 和网络指标")

    ranked = bm25_rank("DBConnectionError 数据库连接失败", [generic, exact], limit=2)

    assert ranked[0].id == "exact"


def test_metadata_recall_matches_service_and_heading():
    checkout = make_document(
        "checkout",
        "数据库排查步骤",
        service_name="checkoutservice",
        h1="数据库故障",
    )
    payment = make_document(
        "payment",
        "支付依赖排查步骤",
        service_name="paymentservice",
        h1="依赖超时",
    )

    ranked = metadata_rank("checkoutservice 当前有什么异常", [payment, checkout], limit=2)

    assert [document.id for document in ranked] == ["checkout"]


def test_rrf_fuses_routes_and_deduplicates_documents():
    first = make_document("first", "CPU 排查")
    second = make_document("second", "数据库排查")

    fused = reciprocal_rank_fusion(
        {
            "dense": [first, second],
            "bm25": [second, first],
            "metadata": [second],
        },
        rrf_k=60,
        top_k=2,
    )

    assert [document.id for document in fused] == ["second", "first"]
    assert fused[0].metadata["_retrieval_routes"] == ["dense", "bm25", "metadata"]
    assert fused[0].metadata["_rrf_score"] > fused[1].metadata["_rrf_score"]


def test_hybrid_retrieval_degrades_when_dense_route_fails():
    documents = [
        make_document(
            "database",
            "数据库连接失败 DBConnectionError",
            service_name="checkoutservice",
        ),
        make_document("latency", "接口延迟升高，检查 CPU 指标"),
    ]

    def failed_dense(_query: str, _limit: int):
        raise RuntimeError("Milvus vector index unavailable")

    service = HybridRetrievalService(
        dense_search=failed_dense,
        corpus_loader=lambda _limit: documents,
    )

    results = service.retrieve("checkoutservice 数据库连接失败", top_k=1)

    assert [document.id for document in results] == ["database"]
    assert set(results[0].metadata["_retrieval_routes"]) == {"bm25", "metadata"}


def test_dense_route_filters_out_incident_memory(monkeypatch):
    captured: dict[str, object] = {}

    class FakeVectorStore:
        def similarity_search(self, query: str, **kwargs):
            captured["query"] = query
            captured.update(kwargs)
            return []

    monkeypatch.setattr(
        hybrid_module.vector_store_manager,
        "get_vector_store",
        lambda: FakeVectorStore(),
    )

    HybridRetrievalService._search_dense("数据库异常", 8)

    assert captured["expr"] == KNOWLEDGE_FILTER_EXPR
    assert captured["k"] == 8


def test_keyword_corpus_excludes_confirmed_incident_memory(monkeypatch):
    class FakeCollection:
        def query(self, **_kwargs):
            return [
                {
                    "id": "knowledge",
                    "content": "数据库排查 Runbook",
                    "metadata": {"_extension": ".md", "_file_name": "database.md"},
                },
                {
                    "id": "incident",
                    "content": "已确认历史故障案例",
                    "metadata": {"_memory_type": "incident_memory"},
                },
            ]

    monkeypatch.setattr(
        hybrid_module.milvus_manager,
        "get_collection",
        lambda: FakeCollection(),
    )

    documents = HybridRetrievalService._load_knowledge_corpus(100)

    assert [document.id for document in documents] == ["knowledge"]
