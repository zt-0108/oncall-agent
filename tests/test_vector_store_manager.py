from langchain_core.documents import Document

from app.services import vector_store_manager as vector_store_module
from app.services.vector_store_manager import VectorStoreManager


def test_replace_writes_new_documents_before_deleting_old(monkeypatch):
    manager = VectorStoreManager()
    events = []

    monkeypatch.setattr(manager, "initialize", lambda: events.append("initialize"))
    monkeypatch.setattr(manager, "_find_ids_by_source", lambda _: ["old-id"])

    def add_documents(_):
        events.append("add")
        return ["new-id"]

    def delete_ids(ids):
        events.append(("delete", ids))
        return len(ids)

    monkeypatch.setattr(manager, "add_documents", add_documents)
    monkeypatch.setattr(manager, "_delete_ids", delete_ids)

    manager.replace_documents_for_source(
        "uploads/notes.txt",
        [Document(page_content="new")],
    )

    assert events == [
        "initialize",
        "add",
        ("delete", ["old-id"]),
    ]


def test_replace_keeps_old_documents_when_new_write_fails(monkeypatch):
    manager = VectorStoreManager()
    deleted = []

    monkeypatch.setattr(manager, "initialize", lambda: None)
    monkeypatch.setattr(manager, "_find_ids_by_source", lambda _: ["old-id"])
    monkeypatch.setattr(
        manager,
        "add_documents",
        lambda _: (_ for _ in ()).throw(RuntimeError("embedding failed")),
    )
    monkeypatch.setattr(manager, "_delete_ids", lambda ids: deleted.extend(ids))

    try:
        manager.replace_documents_for_source(
            "uploads/notes.txt",
            [Document(page_content="new")],
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected write failure")

    assert deleted == []


def test_add_documents_flushes_before_return(monkeypatch):
    manager = VectorStoreManager()
    events = []

    class Store:
        def add_documents(self, documents, ids):
            events.append(("add", len(documents), len(ids)))
            return ids

    class Collection:
        def flush(self):
            events.append("flush")

    monkeypatch.setattr(manager, "initialize", lambda: Store())
    monkeypatch.setattr(vector_store_module.milvus_manager, "get_collection", lambda: Collection())

    manager.add_documents([Document(page_content="memory")])

    assert events == [("add", 1, 1), "flush"]


def test_delete_ids_flushes_collection(monkeypatch):
    manager = VectorStoreManager()
    events = []

    class Result:
        delete_count = 1

    class Collection:
        def delete(self, expression):
            events.append(("delete", expression))
            return Result()

        def flush(self):
            events.append("flush")

    monkeypatch.setattr(vector_store_module.milvus_manager, "get_collection", lambda: Collection())

    assert manager._delete_ids(["memory-id"]) == 1
    assert events == [("delete", 'id in ["memory-id"]'), "flush"]
