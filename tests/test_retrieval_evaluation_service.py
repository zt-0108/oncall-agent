from langchain_core.documents import Document

from app.services.retrieval_evaluation_service import (
    RetrievalEvalCase,
    evaluate_retriever,
)


def test_evaluation_calculates_ranking_metrics_and_rewrite_rate():
    cases = [
        RetrievalEvalCase("one", "cpu", ("cpu.md",)),
        RetrievalEvalCase("two", "memory", ("memory.md",)),
    ]

    def retrieve(query: str, _top_k: int) -> list[Document]:
        if query == "cpu":
            return [
                Document(
                    page_content="cpu",
                    metadata={"_file_name": "cpu.md", "_query_rewrite_applied": True},
                )
            ]
        return [
            Document(page_content="other", metadata={"_file_name": "other.md"}),
            Document(page_content="memory", metadata={"_file_name": "memory.md"}),
        ]

    summary = evaluate_retriever(retrieve, cases)

    assert summary.case_count == 2
    assert summary.hit_rate_at_3 == 1.0
    assert summary.recall_at_8 == 1.0
    assert summary.mrr_at_8 == 0.75
    assert summary.rewrite_rate == 0.5
    assert summary.cases[1].retrieved_sources == ("other.md", "memory.md")
