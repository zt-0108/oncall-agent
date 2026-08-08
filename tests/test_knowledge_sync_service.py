from pathlib import Path

from app.services.knowledge_sync_service import load_manifest, sync_knowledge_documents


def test_sync_uploads_only_changed_documents(tmp_path: Path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "cpu.md").write_text("version 1", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    uploaded: list[str] = []

    def uploader(path: Path, _url: str, _timeout: float, _force: bool) -> None:
        uploaded.append(path.name)

    first = sync_knowledge_documents(
        docs_dir, manifest_path, "http://example/upload", uploader=uploader
    )
    second = sync_knowledge_documents(
        docs_dir, manifest_path, "http://example/upload", uploader=uploader
    )
    (docs_dir / "cpu.md").write_text("version 2", encoding="utf-8")
    third = sync_knowledge_documents(
        docs_dir, manifest_path, "http://example/upload", uploader=uploader
    )

    assert first.uploaded == ["cpu.md"]
    assert second.skipped == ["cpu.md"]
    assert third.uploaded == ["cpu.md"]
    assert uploaded == ["cpu.md", "cpu.md"]
    assert load_manifest(manifest_path)["cpu.md"]


def test_failed_upload_is_not_marked_as_synced(tmp_path: Path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "broken.md").write_text("content", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"

    def uploader(_path: Path, _url: str, _timeout: float, _force: bool) -> None:
        raise RuntimeError("embedding timeout")

    result = sync_knowledge_documents(
        docs_dir, manifest_path, "http://example/upload", uploader=uploader
    )

    assert result.failed == {"broken.md": "embedding timeout"}
    assert load_manifest(manifest_path) == {}
