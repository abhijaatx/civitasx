"""Grounded civic research for the local-first prototype.

The default index is intentionally local and deterministic.  It combines a
keyword score with a small hashed semantic vector so that the application can
be developed without model calls.  A future Bedrock embedding provider can be
plugged into :class:`ResearchIndex` without changing the API contract.

Every returned claim is copied from a retrieved page (or its retained
translation).  The service does not ask an LLM to invent a summary before it
has evidence, which makes missing and conflicting records visible to users.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import re
import threading
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .models import (
    AuthorityRecord,
    DocumentChange,
    DocumentComparison,
    ResearchAnswer,
    ResearchCalculation,
    ResearchCoverage,
    ResearchFact,
    SearchHit,
    SourceDocument,
)

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
SENTENCE_RE = re.compile(r"(?<=[.!?।])\s+|\n+")
PAGE_NUMBER_RE = re.compile(r"(?im)^\s*(?:page\s*)?\d+(?:\s+of\s+\d+)?\s*$")
AMOUNT_RE = re.compile(
    r"(?P<symbol>₹|Rs\.?|INR)\s*(?P<number>[\d,]+(?:\.\d+)?)\s*"
    r"(?P<unit>crore|crores|lakh|lakhs|million|billion)?",
    re.IGNORECASE,
)

STOP_WORDS = {
    "a",
    "about",
    "after",
    "all",
    "an",
    "and",
    "are",
    "at",
    "be",
    "by",
    "can",
    "does",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "near",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "what",
    "when",
    "which",
    "who",
    "will",
    "with",
    "my",
}

# These are query expansions, not facts.  They improve offline recall while
# the page text remains the only source for a returned claim.
SEMANTIC_ALIASES: dict[str, set[str]] = {
    "walking": {"footpath", "pedestrian", "crossing", "sidewalk"},
    "footpath": {"walking", "pedestrian", "crossing"},
    "metro": {"rail", "station", "corridor", "bmrcl"},
    "transit": {"metro", "bus", "route", "station"},
    "zoning": {"landuse", "residential", "building", "setback", "height"},
    "building": {"height", "setback", "parking", "plan"},
    "budget": {"allocation", "cost", "expenditure", "fund"},
    "complaint": {"grievance", "service", "issue", "report"},
    "road": {"junction", "street", "footpath", "safety"},
}

ROUTE_KEYWORDS: dict[str, set[str]] = {
    "transport": {
        "metro",
        "bmrcl",
        "bus",
        "transport",
        "transit",
        "route",
        "station",
        "corridor",
        "fare",
        "ridership",
        "rail",
    },
    "budget": {
        "budget",
        "allocation",
        "cost",
        "expenditure",
        "tax",
        "fund",
        "spend",
        "financial",
        "crore",
        "lakh",
    },
    "planning": {
        "zoning",
        "zone",
        "parcel",
        "land",
        "building",
        "height",
        "setback",
        "parking",
        "residential",
        "layout",
        "development",
        "survey",
    },
    "civic_service": {
        "complaint",
        "garbage",
        "waste",
        "drain",
        "road",
        "footpath",
        "pedestrian",
        "path",
        "streetlight",
        "property",
        "ward",
        "grievance",
        "water",
    },
}

ROUTE_AUTHORITIES = {
    "transport": {"bmrcl", "gba"},
    "budget": {"gba", "bda", "bmrcl"},
    "planning": {"bda", "gba"},
    "civic_service": {"gba"},
    "general": {"gba", "bda", "bmrcl"},
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def route_goal(question: str) -> str:
    """Route a goal to a civic topic without requiring an LLM call."""

    tokens = set(_tokens(question))
    scores = {route: len(tokens & keywords) for route, keywords in ROUTE_KEYWORDS.items()}
    best_route, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score == 0:
        return "general"
    return best_route


def _tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return [token for token in TOKEN_RE.findall(normalized) if token not in STOP_WORDS]


def _expanded_tokens(text: str) -> list[str]:
    base = _tokens(text)
    expanded = list(base)
    for token in base:
        expanded.extend(SEMANTIC_ALIASES.get(token, ()))
    return expanded


def _hash_vector(text: str, size: int = 128) -> list[float]:
    vector = [0.0] * size
    for token in _expanded_tokens(text):
        digest = hashlib.sha1(token.encode("utf-8"), usedforsecurity=False).digest()
        index = int.from_bytes(digest[:4], "big") % size
        vector[index] += 1.0
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    return max(0.0, sum(a * b for a, b in zip(left, right, strict=True)))


def _canonical_pages(pages: Iterable[dict[str, Any]]) -> str:
    return "\n".join(
        f"{page.get('page', index + 1)}\n{page.get('text', '')}\n"
        f"{page.get('original_text', '')}\n{page.get('translation', '')}"
        for index, page in enumerate(pages)
    )


def content_hash(pages: Iterable[dict[str, Any]]) -> str:
    return hashlib.sha256(_canonical_pages(pages).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CorpusPage:
    source_id: str
    page: int
    text: str
    original_text: str | None = None
    translation: str | None = None
    language: str = "en"

    @property
    def searchable_text(self) -> str:
        return " ".join(part for part in (self.text, self.original_text, self.translation) if part)


class ResearchIndex:
    """Read a versioned corpus manifest and answer evidence-backed queries."""

    def __init__(
        self,
        manifest_path: str | Path,
        stale_after_days: int = 180,
        live_cache_path: str | Path | None = None,
        live_cache_bucket: str | None = None,
        live_cache_prefix: str = "runtime/live-sources/",
    ):
        self.manifest_path = Path(manifest_path).expanduser()
        self.live_cache_path = Path(live_cache_path).expanduser() if live_cache_path else None
        self.live_cache_bucket = live_cache_bucket.strip() if live_cache_bucket else None
        self.live_cache_prefix = live_cache_prefix.strip("/") + "/"
        self.stale_after_days = max(1, stale_after_days)
        self.documents: dict[str, SourceDocument] = {}
        self.pages: list[CorpusPage] = []
        self.authority_records: dict[str, AuthorityRecord] = {}
        self.hash_mismatches: set[str] = set()
        self._live_lock = threading.RLock()
        self._s3_client: Any | None = None
        self.reload()

    def reload(self) -> None:
        with self._live_lock:
            self.documents.clear()
            self.pages.clear()
            self.authority_records.clear()
            self.hash_mismatches.clear()
            if not self.manifest_path.exists():
                return
            raw_manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            for raw_authority in raw_manifest.get("authorities", []):
                record = AuthorityRecord.model_validate(raw_authority)
                self.authority_records[record.authority_id] = record
            raw_documents = list(raw_manifest.get("documents", []))
            raw_documents.extend(self._read_live_documents())
            for raw_document in raw_documents:
                raw_pages = raw_document.get("pages", [])
                expected_page_hash = str(raw_document.get("page_hash", ""))
                if expected_page_hash and expected_page_hash != content_hash(raw_pages):
                    self.hash_mismatches.add(str(raw_document.get("source_id", "")))
                document = SourceDocument.model_validate(
                    {
                        **raw_document,
                        "page_count": len(raw_pages),
                        "extraction_status": (
                            "partial"
                            if raw_document.get("source_id") in self.hash_mismatches
                            else raw_document.get("extraction_status", "unknown")
                        ),
                    }
                )
                self.documents[document.source_id] = document
                for index, raw_page in enumerate(raw_pages, start=1):
                    page_number = int(raw_page.get("page", index))
                    text = str(raw_page.get("text", "")).strip()
                    original = raw_page.get("original_text")
                    translation = raw_page.get("translation")
                    page_language = str(raw_page.get("language", document.language))
                    if not text and original:
                        text = str(original).strip()
                    self.pages.append(
                        CorpusPage(
                            source_id=document.source_id,
                            page=page_number,
                            text=text,
                            original_text=str(original).strip() if original else None,
                            translation=str(translation).strip() if translation else None,
                            language=page_language,
                        )
                    )

    def _s3(self) -> Any:
        if self._s3_client is None:
            import boto3

            self._s3_client = boto3.client("s3")
        return self._s3_client

    def _read_live_documents(self) -> list[dict[str, Any]]:
        if self.live_cache_bucket:
            try:
                paginator = self._s3().get_paginator("list_objects_v2")
                documents: list[dict[str, Any]] = []
                for page in paginator.paginate(
                    Bucket=self.live_cache_bucket, Prefix=self.live_cache_prefix
                ):
                    for item in page.get("Contents", []):
                        key = str(item.get("Key", ""))
                        if not key.endswith(".json"):
                            continue
                        body = self._s3().get_object(Bucket=self.live_cache_bucket, Key=key)[
                            "Body"
                        ].read()
                        decoded = json.loads(body.decode("utf-8"))
                        if isinstance(decoded, dict):
                            documents.append(decoded)
                return documents
            except Exception:  # pragma: no cover - provider-specific S3 errors
                # A warm local cache is still useful while an S3 endpoint is
                # temporarily unavailable; production never invents records.
                pass
        if self.live_cache_path and self.live_cache_path.exists():
            try:
                live_documents = json.loads(self.live_cache_path.read_text(encoding="utf-8"))
                if isinstance(live_documents, list):
                    return [item for item in live_documents if isinstance(item, dict)]
            except (OSError, json.JSONDecodeError):
                pass
        return []

    def add_live_entry(self, entry: dict[str, Any]) -> SourceDocument:
        """Persist one fetched official source and reload the searchable index."""

        if not self.live_cache_path:
            if not self.live_cache_bucket:
                raise RuntimeError("A live cache path or S3 bucket is required")
        with self._live_lock:
            source_id = str(entry.get("source_id", ""))
            if not source_id or not re.fullmatch(r"[A-Za-z0-9._-]+", source_id):
                raise ValueError("Fetched source has an invalid source_id")
            if self.live_cache_bucket:
                self._s3().put_object(
                    Bucket=self.live_cache_bucket,
                    Key=f"{self.live_cache_prefix}{source_id}.json",
                    Body=(json.dumps(entry, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                    ContentType="application/json",
                )
            else:
                assert self.live_cache_path is not None
                self.live_cache_path.parent.mkdir(parents=True, exist_ok=True)
                entries: list[dict[str, Any]] = []
                if self.live_cache_path.exists():
                    try:
                        raw_entries = json.loads(self.live_cache_path.read_text(encoding="utf-8"))
                        if isinstance(raw_entries, list):
                            entries = [item for item in raw_entries if isinstance(item, dict)]
                    except (OSError, json.JSONDecodeError):
                        entries = []
                entries = [item for item in entries if str(item.get("source_id", "")) != source_id]
                entries.append(entry)
                temporary = self.live_cache_path.with_suffix(self.live_cache_path.suffix + ".tmp")
                temporary.write_text(
                    json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                temporary.replace(self.live_cache_path)
            self.reload()
        document = self.get_document(source_id)
        if document is None:
            raise RuntimeError("Fetched source could not be indexed")
        return document

    @property
    def document_count(self) -> int:
        return len(self.documents)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def get_document(self, source_id: str) -> SourceDocument | None:
        return self.documents.get(source_id)

    def compare_documents(self, source_id: str, baseline_source_id: str) -> DocumentComparison:
        """Compare two indexed versions while keeping every change page-bound.

        The local implementation is intentionally conservative: it reports
        textual additions/removals and labels the interpretation as uncertain.
        A future parser can add structured ordinance fields without changing
        this source-linked contract.
        """

        current = self.documents.get(source_id)
        baseline = self.documents.get(baseline_source_id)
        if current is None or baseline is None:
            missing = source_id if current is None else baseline_source_id
            raise KeyError(f"Source document not found: {missing}")
        current_pages = [
            (page.page, page.text.strip()) for page in self.pages if page.source_id == source_id
        ]
        baseline_pages = [
            (page.page, page.text.strip())
            for page in self.pages
            if page.source_id == baseline_source_id
        ]
        changes: list[DocumentChange] = []

        def alignment_key(text: str) -> str:
            # PDF extractors often include a running page number as a separate
            # line. It is not document content and would otherwise defeat the
            # sequence alignment when a cover page shifts every page number.
            without_page_number = PAGE_NUMBER_RE.sub("", text)
            return " ".join(without_page_number.split())

        # Align pages by their content before comparing them.  Page numbers in
        # a PDF are presentation coordinates, not stable identities: a newly
        # inserted cover page must not make every later page look rewritten.
        matcher = SequenceMatcher(
            a=[alignment_key(text) for _, text in baseline_pages],
            b=[alignment_key(text) for _, text in current_pages],
            autojunk=False,
        )
        for (
            opcode,
            baseline_start,
            baseline_end,
            current_start,
            current_end,
        ) in matcher.get_opcodes():
            if opcode == "equal":
                continue
            if opcode == "delete":
                for baseline_index in range(baseline_start, baseline_end):
                    page_number, before = baseline_pages[baseline_index]
                    changes.append(
                        DocumentChange(
                            page=page_number,
                            change_type="removed",
                            before=before,
                            after="",
                            source_id=source_id,
                            baseline_source_id=baseline_source_id,
                        )
                    )
                continue
            if opcode == "insert":
                for current_index in range(current_start, current_end):
                    page_number, after = current_pages[current_index]
                    changes.append(
                        DocumentChange(
                            page=page_number,
                            change_type="added",
                            before="",
                            after=after,
                            source_id=source_id,
                            baseline_source_id=baseline_source_id,
                        )
                    )
                continue

            # A replace can contain both a content edit and a page insertion or
            # removal. Pair the overlapping pages and report the remainder in
            # its proper document coordinate.
            overlap = min(baseline_end - baseline_start, current_end - current_start)
            for offset in range(overlap):
                baseline_page, before = baseline_pages[baseline_start + offset]
                current_page, after = current_pages[current_start + offset]
                if before == after:
                    continue
                ratio = SequenceMatcher(a=before, b=after, autojunk=False).ratio()
                if ratio < 0.995:
                    changes.append(
                        DocumentChange(
                            page=current_page,
                            change_type="changed",
                            before=before,
                            after=after,
                            source_id=source_id,
                            baseline_source_id=baseline_source_id,
                        )
                    )
            for baseline_index in range(baseline_start + overlap, baseline_end):
                page_number, before = baseline_pages[baseline_index]
                changes.append(
                    DocumentChange(
                        page=page_number,
                        change_type="removed",
                        before=before,
                        after="",
                        source_id=source_id,
                        baseline_source_id=baseline_source_id,
                    )
                )
            for current_index in range(current_start + overlap, current_end):
                page_number, after = current_pages[current_index]
                changes.append(
                    DocumentChange(
                        page=page_number,
                        change_type="added",
                        before="",
                        after=after,
                        source_id=source_id,
                        baseline_source_id=baseline_source_id,
                    )
                )
        summary = (
            f"{len(changes)} page{'s' if len(changes) != 1 else ''} differ between "
            f"{baseline.title} and {current.title}."
            if changes
            else "No textual differences were found in the indexed pages."
        )
        uncertainties = [
            (
                "This comparison reports indexed text changes; it does not decide "
                "whether a change has legal effect."
            ),
        ]
        if current.status in {"draft", "proposed"}:
            uncertainties.append("The current document is not marked as an adopted rule.")
        if source_id in self.hash_mismatches or baseline_source_id in self.hash_mismatches:
            uncertainties.append(
                "At least one document failed its page hash check and should be refreshed."
            )
        return DocumentComparison(
            source_id=source_id,
            baseline_source_id=baseline_source_id,
            current_title=current.title,
            baseline_title=baseline.title,
            authority=current.authority,
            status=current.status,
            changes=changes,
            summary=summary,
            uncertainties=uncertainties,
            checked_at=utc_now(),
        )

    def list_authorities(self, query: str | None = None) -> list[AuthorityRecord]:
        records = list(self.authority_records.values())
        if query:
            tokens = set(_tokens(query))
            scored = []
            for record in records:
                haystack = " ".join(
                    [record.name, record.short_name, *record.responsibilities]
                ).casefold()
                score = sum(1 for token in tokens if token in haystack)
                scored.append((score, record))
            records = [
                record
                for score, record in sorted(scored, key=lambda item: item[0], reverse=True)
                if score
            ]
        return records

    def authority_matches(
        self, question: str, route: str, hits: Sequence[SearchHit]
    ) -> list[AuthorityRecord]:
        hit_ids = [hit.authority_id for hit in hits]
        explicit = []
        q_tokens = set(_tokens(question))
        for authority_id, record in self.authority_records.items():
            names = {authority_id, record.short_name.casefold(), *(_tokens(record.name))}
            if q_tokens & names:
                explicit.append(authority_id)
        chosen = list(dict.fromkeys(explicit + hit_ids))
        if not chosen:
            if not hits and route == "general":
                return []
            chosen = [
                authority_id
                for authority_id in ROUTE_AUTHORITIES.get(route, set())
                if authority_id in self.authority_records
            ]
        # Keep a broad question honest: if more than one authority has evidence,
        # label the routing as ambiguous instead of picking a recipient silently.
        routing_words = {"who", "handles", "authority", "responsible", "contact", "department"}
        ambiguous = len(chosen) > 1 and (
            bool(q_tokens & routing_words) or len(explicit) > 1 or not hits
        )
        result = []
        for authority_id in chosen:
            record = self.authority_records.get(authority_id)
            if record:
                result.append(
                    record.model_copy(update={"status": "ambiguous"} if ambiguous else {})
                )
        return result

    def search(
        self,
        question: str,
        *,
        max_sources: int = 6,
        authority_id: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[SearchHit], ResearchCoverage]:
        route = route_goal(question)
        query_tokens = set(_expanded_tokens(question))
        original_tokens = set(_tokens(question))
        if not query_tokens:
            return [], ResearchCoverage(
                documents_considered=0,
                pages_considered=0,
                hits_returned=0,
                retrieval_mode="hybrid_offline",
                source_quality="none",
                missing=["The question did not contain a searchable topic."],
            )
        query_vector = _hash_vector(question)
        candidates: list[tuple[float, float, float, CorpusPage, SourceDocument, list[str]]] = []
        considered_documents: set[str] = set()
        stale_ids: set[str] = set()
        unreadable_ids: set[str] = set()
        now = utc_now()
        for page in self.pages:
            document = self.documents.get(page.source_id)
            if document is None:
                continue
            if (
                route in {"transport", "planning", "civic_service"}
                and document.authority_id not in ROUTE_AUTHORITIES[route]
            ):
                continue
            if authority_id and document.authority_id != authority_id:
                continue
            if date_from and (document.published_at is None or document.published_at < date_from):
                continue
            if date_to and (document.published_at is None or document.published_at > date_to):
                continue
            considered_documents.add(document.source_id)
            if document.retrieved_at and document.retrieved_at < now - timedelta(
                days=self.stale_after_days
            ):
                stale_ids.add(document.source_id)
            if document.extraction_status == "unreadable":
                unreadable_ids.add(document.source_id)
                continue
            page_tokens = _tokens(page.searchable_text)
            counts = Counter(page_tokens)
            matched = sorted(original_tokens & set(page_tokens))
            # Exact query words carry most of the weight.  Alias terms only
            # widen recall (for example, walking → footpath) and must not let
            # a generic ``phase`` or ``project`` sentence outrank the page
            # containing the user's actual subject.
            exact_score = sum(min(3, counts[token]) for token in original_tokens if token in counts)
            alias_tokens = query_tokens - original_tokens
            alias_score = sum(min(2, counts[token]) for token in alias_tokens if token in counts)
            keyword = (exact_score + alias_score * 0.15) / max(1, len(original_tokens))
            if matched:
                keyword += 0.15
            title_tokens = set(_tokens(document.title))
            keyword += 0.2 if original_tokens & title_tokens else 0
            route_authorities = ROUTE_AUTHORITIES.get(route, set())
            keyword += 0.08 if document.authority_id in route_authorities else 0
            semantic = _cosine(query_vector, _hash_vector(page.searchable_text))
            combined = max(0.0, keyword * 0.68 + semantic * 0.32)
            # A result must have a lexical match or a meaningful semantic score;
            # this prevents a generic page from being shown for an unsupported ask.
            if not matched and semantic < 0.30:
                continue
            candidates.append((combined, keyword, semantic, page, document, matched))

        candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        hits: list[SearchHit] = []
        seen_sources: set[str] = set()
        source_terms: dict[str, set[str]] = defaultdict(set)
        seen_pages: set[tuple[str, int]] = set()
        for combined, keyword, semantic, page, document, matched in candidates:
            page_key = (document.source_id, page.page)
            if page_key in seen_pages:
                continue
            novel_terms = set(matched) - source_terms.get(document.source_id, set())
            # Return a second page from one document when it supports a new
            # query term (for example, a corridor page plus its cost page).
            # This keeps citations useful without dumping every page.
            if document.source_id in seen_sources and (
                not novel_terms or len([h for h in hits if h.source_id == document.source_id]) >= 2
            ):
                continue
            seen_sources.add(document.source_id)
            seen_pages.add(page_key)
            source_terms.setdefault(document.source_id, set()).update(matched)
            has_translation = bool(page.translation and page.original_text)
            passage = page.translation if has_translation else page.text
            hits.append(
                SearchHit(
                    source_id=document.source_id,
                    title=document.title,
                    authority=document.authority,
                    authority_id=document.authority_id,
                    status=document.status,
                    page=page.page,
                    score=round(combined, 5),
                    keyword_score=round(max(0, keyword), 5),
                    semantic_score=round(max(0, semantic), 5),
                    passage=passage,
                    original_passage=page.original_text if has_translation else None,
                    translated_passage=page.translation if has_translation else None,
                    translation_language="kn" if has_translation else None,
                    published_at=document.published_at,
                    retrieved_at=document.retrieved_at,
                    url=document.url,
                    content_hash=document.content_hash,
                    matched_terms=matched,
                    source_kind=document.source_kind,
                    extraction_method=document.extraction_method,
                    extraction_status=document.extraction_status,
                )
            )
            if len(hits) >= max(1, min(max_sources, 12)):
                break
        qualities = {hit.source_kind for hit in hits}
        if not hits:
            quality = "none"
        elif qualities == {"official"}:
            quality = "official"
        elif qualities <= {"official_reference"}:
            quality = "official_reference"
        else:
            quality = "mixed"
        coverage = ResearchCoverage(
            documents_considered=len(considered_documents),
            pages_considered=sum(
                1 for page in self.pages if page.source_id in considered_documents
            ),
            hits_returned=len(hits),
            retrieval_mode="hybrid_offline",
            source_quality=quality,
            stale_source_ids=sorted(stale_ids),
            unreadable_source_ids=sorted(unreadable_ids),
            missing=[]
            if hits
            else ["No indexed passage met the evidence threshold for this question."],
        )
        return hits, coverage

    def answer(
        self,
        question: str,
        *,
        max_sources: int = 6,
        authority_id: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> ResearchAnswer:
        checked_at = utc_now()
        route = route_goal(question)
        hits, coverage = self.search(
            question,
            max_sources=max_sources,
            authority_id=authority_id,
            date_from=date_from,
            date_to=date_to,
        )
        sources = [hit.as_evidence() for hit in hits]
        facts: list[ResearchFact] = []
        uncertainties: list[str] = []
        for index, hit in enumerate(hits, start=1):
            claim = _supported_sentence(hit.passage)
            if not claim:
                continue
            facts.append(
                ResearchFact(
                    fact_id=f"fact-{index}",
                    claim=claim,
                    supporting_source_ids=[hit.source_id],
                    supporting_pages=[hit.page],
                    claim_status="supported",
                )
            )
        statuses = {hit.status for hit in hits}
        if "proposed" in statuses or "draft" in statuses:
            uncertainties.append(
                "Some matching records are proposals or drafts; they do not "
                "change an adopted rule by themselves."
            )
        if "historical" in statuses:
            uncertainties.append(
                "At least one matching record is historical. Check the newest "
                "adopted record before acting."
            )
        if coverage.stale_source_ids:
            uncertainties.append(
                "One or more matching sources may be stale because their retrieval "
                "time is outside the refresh window."
            )
        if coverage.unreadable_source_ids:
            uncertainties.append(
                "Some indexed material could not be read and was excluded from the answer."
            )
        if len({hit.authority_id for hit in hits}) > 1:
            coverage.conflicts.append(
                "The evidence spans more than one authority; responsibility should "
                "be confirmed before sending a request."
            )
            uncertainties.append(
                "The evidence spans multiple authorities. The directory keeps that "
                "routing visible instead of choosing a recipient silently."
            )
        if not hits:
            uncertainties.extend(coverage.missing)
            if not coverage.missing:
                uncertainties.append("No responsible authority was verified for this question.")
            explanation = (
                "I could not find a supporting passage in the indexed Bengaluru "
                "record for this question. No claim is shown as an answer; add an "
                "official document or try a more specific topic."
            )
        else:
            authority_names = ", ".join(dict.fromkeys(hit.authority for hit in hits))
            explanation = (
                f"I found {len(hits)} supporting passage{'' if len(hits) == 1 else 's'} "
                f"from {authority_names}. The facts below are copied from those "
                "pages; open a source to inspect its status, dates and passage."
            )
        return ResearchAnswer(
            query=question,
            route=route,  # type: ignore[arg-type]
            explanation=explanation,
            facts=facts,
            uncertainties=uncertainties,
            sources=sources,
            authority_matches=self.authority_matches(question, route, hits),
            coverage=coverage,
            calculations=_calculations_from_hits(hits),
            checked_at=checked_at,
            generated_by="extractive",
        )


def _supported_sentence(text: str) -> str:
    clean = " ".join(html.unescape(text).split()).strip()
    if not clean:
        return ""
    pieces = [piece.strip() for piece in SENTENCE_RE.split(clean) if piece.strip()]
    sentence = pieces[0] if pieces else clean
    # Keep source wording intact while preventing an unbounded page dump.
    if len(sentence) > 420:
        sentence = sentence[:417].rstrip() + "…"
    return sentence


def _calculations_from_hits(hits: Sequence[SearchHit]) -> list[ResearchCalculation]:
    calculations: list[ResearchCalculation] = []
    seen: set[str] = set()
    for hit in hits:
        for match in AMOUNT_RE.finditer(hit.passage):
            number_text = match.group("number").replace(",", "")
            try:
                number = float(number_text)
            except ValueError:
                continue
            unit = (match.group("unit") or "").casefold()
            multiplier = {
                "crore": 10_000_000,
                "crores": 10_000_000,
                "lakh": 100_000,
                "lakhs": 100_000,
                "million": 1_000_000,
                "billion": 1_000_000_000,
            }.get(unit, 1)
            rupees = number * multiplier
            if multiplier == 1:
                result = f"₹{number:,.0f}"
            else:
                result = f"₹{rupees:,.0f}"
            expression = match.group(0).strip()
            key = f"{expression}|{result}|{hit.source_id}"
            if key in seen:
                continue
            seen.add(key)
            calculations.append(
                ResearchCalculation(
                    expression=expression, result=result, source_ids=[hit.source_id]
                )
            )
    return calculations[:12]


class _VisibleTextParser(HTMLParser):
    """Small dependency-free HTML text extractor used by the ingest command."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "template"}:
            self._hidden_depth += 1
        if tag.lower() in {"p", "div", "li", "br", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "template"}:
            self._hidden_depth = max(0, self._hidden_depth - 1)
        if tag.lower() in {"p", "div", "li", "br", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._hidden_depth == 0:
            self.parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


@dataclass(frozen=True)
class ExtractedPage:
    page: int
    text: str
    language: str = "en"
    original_text: str | None = None
    translation: str | None = None


class DocumentIngestor:
    """Extract an official PDF/HTML/text file and produce a manifest entry.

    OCR and translation providers are optional callables.  If OCR is not
    installed, the document is retained with ``unreadable`` status instead of
    being silently dropped or fabricated.  The original bytes can be copied to
    ``raw/`` by :meth:`write_bundle`.
    """

    def __init__(
        self,
        *,
        translation_cache: TranslationCache | None = None,
        ocr_provider: Callable[[bytes, int], str] | None = None,
        translation_provider: Callable[[str, str], str] | None = None,
    ):
        self.translation_cache = translation_cache
        self.ocr_provider = ocr_provider
        self.translation_provider = translation_provider

    def extract(
        self, raw: bytes, *, content_type: str = "text/plain", language: str = "en"
    ) -> tuple[list[ExtractedPage], str, str]:
        kind = content_type.casefold().split(";", 1)[0].strip()
        if kind == "application/pdf" or raw[:4] == b"%PDF":
            pages, method = self._extract_pdf(raw, language)
        elif kind in {"text/html", "application/xhtml+xml"} or b"<html" in raw[:500].lower():
            parser = _VisibleTextParser()
            parser.feed(raw.decode("utf-8", errors="replace"))
            pages, method = (
                [ExtractedPage(page=1, text=parser.text(), language=language)],
                "html_text",
            )
        elif kind in {"application/json", "text/json"}:
            try:
                text = json.dumps(json.loads(raw.decode("utf-8")), ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                text = raw.decode("utf-8", errors="replace")
            pages, method = [ExtractedPage(page=1, text=text, language=language)], "json_text"
        else:
            chunks = [
                chunk.strip()
                for chunk in re.split(r"\f+", raw.decode("utf-8", errors="replace"))
                if chunk.strip()
            ]
            pages, method = (
                [
                    ExtractedPage(page=index, text=chunk, language=language)
                    for index, chunk in enumerate(chunks or [""], start=1)
                ],
                "native_text",
            )
        translated_pages: list[ExtractedPage] = []
        for page in pages:
            if page.language.casefold() in {"kn", "kannada", "mixed"} and page.text:
                translation = page.translation
                if not translation and self.translation_cache:
                    translation = self.translation_cache.translate(
                        page.text, "kn", self.translation_provider
                    )
                translated_pages.append(
                    ExtractedPage(
                        page=page.page,
                        text=page.text,
                        language=page.language,
                        original_text=page.original_text or page.text,
                        translation=translation,
                    )
                )
            else:
                translated_pages.append(page)
        status = "complete" if any(page.text for page in translated_pages) else "unreadable"
        return translated_pages, method, status

    def _extract_pdf(self, raw: bytes, language: str) -> tuple[list[ExtractedPage], str]:
        try:
            from io import BytesIO

            from pypdf import PdfReader

            reader = PdfReader(BytesIO(raw))
            pages = [
                ExtractedPage(
                    page=index, text=(page.extract_text() or "").strip(), language=language
                )
                for index, page in enumerate(reader.pages, start=1)
            ]
            if any(page.text for page in pages):
                return pages, "pypdf_native"
        except Exception:
            pages = []
        if self.ocr_provider:
            ocr_pages = []
            for index in range(1, max(1, len(pages)) + 1):
                text = self.ocr_provider(raw, index).strip()
                ocr_pages.append(ExtractedPage(page=index, text=text, language=language))
            return ocr_pages, "ocr"
        return pages or [ExtractedPage(page=1, text="", language=language)], "unreadable"

    def manifest_entry(
        self,
        *,
        source_id: str,
        title: str,
        authority: str,
        authority_id: str,
        raw: bytes,
        pages: Sequence[ExtractedPage],
        url: str,
        status: str = "unknown",
        published_at: datetime | None = None,
        retrieved_at: datetime | None = None,
        source_kind: str = "official",
        extraction_method: str = "unknown",
        extraction_status: str = "unknown",
    ) -> dict[str, Any]:
        page_dicts = [
            {
                "page": page.page,
                "text": page.text,
                "language": page.language,
                **({"original_text": page.original_text} if page.original_text else {}),
                **({"translation": page.translation} if page.translation else {}),
            }
            for page in pages
        ]
        return {
            "source_id": source_id,
            "title": title,
            "authority": authority,
            "authority_id": authority_id,
            "jurisdiction": "Bengaluru",
            "status": status,
            "published_at": published_at.isoformat() if published_at else None,
            "retrieved_at": (retrieved_at or utc_now()).isoformat(),
            "url": url,
            "language": "mixed"
            if any(page.translation for page in pages)
            else (pages[0].language if pages else "unknown"),
            "source_kind": source_kind,
            "extraction_method": extraction_method,
            "extraction_status": extraction_status,
            "content_hash": hashlib.sha256(raw).hexdigest(),
            "page_hash": content_hash(page_dicts),
            "pages": page_dicts,
        }

    def write_bundle(
        self, output_dir: str | Path, entry: dict[str, Any], raw: bytes, extension: str = "bin"
    ) -> Path:
        output = Path(output_dir)
        (output / "raw").mkdir(parents=True, exist_ok=True)
        (output / "documents").mkdir(parents=True, exist_ok=True)
        source_id = str(entry["source_id"])
        raw_path = output / "raw" / f"{source_id}.{extension.lstrip('.') or 'bin'}"
        raw_path.write_bytes(raw)
        (output / "documents" / f"{source_id}.json").write_text(
            json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return raw_path


class TranslationCache:
    """Content-addressed translation cache retaining the original passage."""

    _FALLBACKS = {
        "ನಗರದ ಪಾದಚಾರಿ ಮಾರ್ಗ ಸುಧಾರಣಾ ಯೋಜನೆಗೆ ರೂ. ೨೫ ಕೋಟಿ ಮೀಸಲಿಡಲಾಗಿದೆ.": (
            "The pedestrian path improvement programme has an allocation of ₹25 crore."
        ),
    }

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._cache: dict[str, str] = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self._cache = {}

    def translate(
        self,
        original: str,
        language: str,
        provider: Callable[[str, str], str] | None = None,
    ) -> str | None:
        if language.casefold() not in {"kn", "kannada", "mixed"}:
            return original
        key = f"{language.casefold()}:{hashlib.sha256(original.encode('utf-8')).hexdigest()}"
        if key in self._cache:
            return self._cache[key]
        translated = (
            provider(original, language) if provider else self._FALLBACKS.get(original.strip())
        )
        if translated:
            self._cache[key] = translated
            self.path.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        return translated


def default_manifest_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "corpus" / "manifest.json"


def default_translation_cache_path() -> Path:
    return Path(".civitas") / "translation-cache.json"
