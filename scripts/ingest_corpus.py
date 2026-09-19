#!/usr/bin/env python3
"""Ingest one official document into a CivitasX corpus manifest.

The command is deliberately explicit about metadata.  It never guesses an
authority, publication date, document status, or URL from page content.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from datetime import UTC, datetime
from pathlib import Path

API_SRC = Path(__file__).resolve().parents[1] / "services" / "api" / "src"
if str(API_SRC) not in sys.path:
    sys.path.insert(0, str(API_SRC))

from civitas_api.research import DocumentIngestor, TranslationCache


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Downloaded PDF, HTML, JSON, or text file")
    parser.add_argument(
        "--manifest", type=Path, default=Path("services/api/data/corpus/manifest.json")
    )
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--authority", required=True)
    parser.add_argument("--authority-id", required=True, choices=("gba", "bda", "bmrcl"))
    parser.add_argument("--url", required=True)
    parser.add_argument(
        "--status",
        default="unknown",
        choices=("draft", "proposed", "adopted", "historical", "unknown"),
    )
    parser.add_argument("--published-at", type=str)
    parser.add_argument("--language", default="en", choices=("en", "kn", "mixed", "unknown"))
    parser.add_argument(
        "--source-kind",
        default="official",
        choices=("official", "official_reference", "uploaded", "fixture"),
    )
    parser.add_argument("--corpus-dir", type=Path, default=Path("services/api/data/corpus"))
    parser.add_argument(
        "--translation-cache", type=Path, default=Path(".civitas/translation-cache.json")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw = args.path.read_bytes()
    content_type = mimetypes.guess_type(args.path.name)[0] or "application/octet-stream"
    ingestor = DocumentIngestor(translation_cache=TranslationCache(args.translation_cache))
    pages, method, extraction_status = ingestor.extract(
        raw, content_type=content_type, language=args.language
    )
    published_at = (
        datetime.fromisoformat(args.published_at).astimezone(UTC) if args.published_at else None
    )
    entry = ingestor.manifest_entry(
        source_id=args.source_id,
        title=args.title,
        authority=args.authority,
        authority_id=args.authority_id,
        raw=raw,
        pages=pages,
        url=args.url,
        status=args.status,
        published_at=published_at,
        retrieved_at=datetime.now(UTC),
        source_kind=args.source_kind,
        extraction_method=method,
        extraction_status=extraction_status,
    )
    ingestor.write_bundle(args.corpus_dir, entry, raw, args.path.suffix or ".bin")
    manifest = (
        json.loads(args.manifest.read_text(encoding="utf-8"))
        if args.manifest.exists()
        else {"version": 1, "documents": [], "authorities": []}
    )
    manifest.setdefault("documents", [])
    manifest["documents"] = [
        doc for doc in manifest["documents"] if doc.get("source_id") != args.source_id
    ]
    manifest["documents"].append(entry)
    manifest["documents"].sort(key=lambda doc: str(doc.get("source_id", "")))
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "source_id": args.source_id,
                "pages": len(pages),
                "extraction_method": method,
                "extraction_status": extraction_status,
                "content_hash": entry["content_hash"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
