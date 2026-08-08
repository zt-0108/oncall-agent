from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import file as file_api


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(file_api.router)
    return TestClient(app)


def test_oversized_upload_keeps_existing_file(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    existing = upload_dir / "notes.txt"
    existing.write_bytes(b"old")

    monkeypatch.setattr(file_api, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(file_api, "MAX_FILE_SIZE", 4)

    response = _client().post(
        "/upload",
        files={"file": ("notes.txt", b"12345", "text/plain")},
    )

    assert response.status_code == 400
    assert existing.read_bytes() == b"old"
    assert list(upload_dir.glob("*.upload")) == []


def test_directory_index_rejects_path_outside_uploads(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    outside_dir = tmp_path / "outside"
    upload_dir.mkdir()
    outside_dir.mkdir()
    monkeypatch.setattr(file_api, "UPLOAD_DIR", upload_dir)

    response = _client().post(
        "/index_directory",
        params={"directory_path": str(outside_dir)},
    )

    assert response.status_code == 400


def test_sanitize_filename_removes_client_path():
    assert file_api._sanitize_filename(r"C:\fake\path\notes.txt") == "notes.txt"


def test_identical_upload_skips_reindex(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    (upload_dir / "notes.md").write_bytes(b"same content")
    indexed: list[str] = []
    monkeypatch.setattr(file_api, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(
        file_api.vector_index_service,
        "index_single_file",
        lambda path: indexed.append(path),
    )

    response = _client().post(
        "/upload",
        files={"file": ("notes.md", b"same content", "text/markdown")},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "unchanged"
    assert response.json()["data"]["indexed"] is False
    assert indexed == []


def test_force_upload_reindexes_identical_content(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    (upload_dir / "notes.md").write_bytes(b"same content")
    indexed: list[str] = []
    monkeypatch.setattr(file_api, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(
        file_api.vector_index_service,
        "index_single_file",
        lambda path: indexed.append(path),
    )

    response = _client().post(
        "/upload?force=true",
        files={"file": ("notes.md", b"same content", "text/markdown")},
    )

    assert response.status_code == 200
    assert response.json()["data"]["indexed"] is True
    assert len(indexed) == 1
