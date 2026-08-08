"""Synchronize changed AIOps documents with the vector database."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.services.knowledge_sync_service import sync_knowledge_documents


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs-dir", type=Path, default=Path("aiops-docs"))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("volumes/aiops-docs-manifest.json"),
    )
    parser.add_argument("--upload-url", default="http://127.0.0.1:9900/api/upload")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    print("Synchronizing aiops-docs (content-hash incremental mode)...")
    result = sync_knowledge_documents(
        args.docs_dir,
        args.manifest,
        args.upload_url,
        force=args.force,
        timeout=args.timeout,
    )
    for filename in result.uploaded:
        print(f"  [UPDATED] {filename}")
    for filename in result.skipped:
        print(f"  [SKIPPED] {filename} (unchanged)")
    for filename, error in result.failed.items():
        print(f"  [FAILED]  {filename}: {error}")
    print(
        f"Knowledge sync finished: updated={len(result.uploaded)}, "
        f"unchanged={len(result.skipped)}, failed={len(result.failed)}"
    )
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
