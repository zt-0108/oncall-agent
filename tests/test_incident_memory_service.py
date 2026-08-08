from langchain_core.documents import Document

from app.models.incident_memory import IncidentMemoryCreate
from app.services.incident_memory_service import IncidentMemoryService


class FakeVectorStore:
    def __init__(self) -> None:
        self.documents = {}
        self.deleted = []

    def replace_documents_for_source(self, source, documents) -> None:
        self.documents[source] = documents

    def delete_by_source(self, source) -> int:
        self.deleted.append(source)
        self.documents.pop(source, None)
        return 1

    def get_vector_store(self):
        return self

    def similarity_search_with_score(self, query, k, expr):
        assert query
        assert expr == 'metadata["_memory_type"] == "incident_memory"'
        documents = [documents[0] for documents in self.documents.values()]
        return [(document, 0.25) for document in documents[:k]]


def payload() -> IncidentMemoryCreate:
    return IncidentMemoryCreate(
        session_id="session-1",
        title="订单数据库连接失败",
        service_name="order-service",
        symptoms=["HTTP 500 增加"],
        alerts=["FaultLabDatabaseErrors"],
        key_evidence=["connection refused"],
        confirmed_root_cause="数据库地址配置错误",
        resolution="修正数据库地址并重启连接池",
        verification="错误率恢复且告警解除",
        diagnosis_report="# 诊断报告",
        source="live_local",
        reviewer="tester",
        confirmed=True,
    )


def test_confirmed_incident_is_audited_and_indexed(tmp_path) -> None:
    vectors = FakeVectorStore()
    service = IncidentMemoryService(tmp_path / "incidents.db", vectors)

    memory = service.confirm_and_store(payload())

    assert memory.confirmed_root_cause == "数据库地址配置错误"
    assert service.list_recent()[0].incident_id == memory.incident_id
    document: Document = next(iter(vectors.documents.values()))[0]
    assert document.metadata["_memory_type"] == "incident_memory"
    assert "不能证明当前故障" in document.page_content


def test_similarity_result_comes_from_confirmed_audit_record(tmp_path) -> None:
    vectors = FakeVectorStore()
    service = IncidentMemoryService(tmp_path / "incidents.db", vectors)
    memory = service.confirm_and_store(payload())

    result = service.search_similar("订单服务 HTTP 500", 3)

    assert result[0].incident_id == memory.incident_id
    assert result[0].score == 0.25


def test_delete_removes_vector_and_audit_record(tmp_path) -> None:
    vectors = FakeVectorStore()
    service = IncidentMemoryService(tmp_path / "incidents.db", vectors)
    memory = service.confirm_and_store(payload())

    service.delete(memory.incident_id)

    assert vectors.deleted == [memory.vector_source]
    assert service.list_recent() == []
