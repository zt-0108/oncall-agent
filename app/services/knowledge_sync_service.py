"""Incrementally synchronize local knowledge documents through the upload API."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

Manifest = dict[str, str]
UploadCallable = Callable[[Path, str, float, bool], None]


@dataclass
class KnowledgeSyncResult:
    uploaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return not self.failed


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> Manifest:
    if not path.exists():
        return {}
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    documents = payload.get("documents", {}) if isinstance(payload, dict) else {}
    if not isinstance(documents, dict):
        return {}
    return {
        str(name): str(digest)
        for name, digest in documents.items()
        if isinstance(name, str) and isinstance(digest, str)
    }


def save_manifest(path: Path, manifest: Manifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps({"version": 1, "documents": manifest}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def upload_document(path: Path, upload_url: str, timeout: float, force: bool) -> None:
    with path.open("rb") as content, httpx.Client(timeout=timeout) as client:
        response = client.post(
            upload_url,
            params={"force": str(force).lower()},
            files={"file": (path.name, content, "text/markdown")},
        )
        response.raise_for_status()


def sync_knowledge_documents(
    docs_dir: Path,
    manifest_path: Path,
    upload_url: str,
    *,
    force: bool = False,
    timeout: float = 120.0,
    uploader: UploadCallable = upload_document,
) -> KnowledgeSyncResult:
    if not docs_dir.is_dir():
        raise ValueError(f"Knowledge directory does not exist: {docs_dir}")

    manifest = load_manifest(manifest_path)
    updated_manifest = dict(manifest)
    result = KnowledgeSyncResult()

    for path in sorted(docs_dir.glob("*.md")):
        digest = file_sha256(path)
        if not force and manifest.get(path.name) == digest:
            result.skipped.append(path.name)
            continue
        try:
            uploader(path, upload_url, timeout, force)
        except Exception as exc:
            result.failed[path.name] = str(exc)
            continue
        updated_manifest[path.name] = digest
        result.uploaded.append(path.name)

    if updated_manifest != manifest:
        save_manifest(manifest_path, updated_manifest)
    return result
