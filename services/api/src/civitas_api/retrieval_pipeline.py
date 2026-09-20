"""Optional Haystack retrieval adapter for the local civic corpus."""

from __future__ import annotations

from typing import Any

try:  # Optional architecture dependency group.
    from haystack import Document
    from haystack.components.retrievers.in_memory import InMemoryBM25Retriever
    from haystack.document_stores.in_memory import InMemoryDocumentStore
except ImportError:  # pragma: no cover - minimal install fallback
    Document = InMemoryBM25Retriever = InMemoryDocumentStore = None  # type: ignore[assignment]


class HaystackEvidencePipeline:
    """Build a filterable lexical retrieval pipeline over indexed corpus pages."""

    def __init__(self, pages: list[Any]):
        if Document is None or InMemoryDocumentStore is None or InMemoryBM25Retriever is None:
            self.store = None
            self.retriever = None
            return
        self.store = InMemoryDocumentStore()
        documents = [
            Document(
                content=page.searchable_text,
                meta={"source_id": page.source_id, "page": page.page},
            )
            for page in pages
            if page.searchable_text
        ]
        if documents:
            self.store.write_documents(documents)
        self.retriever = InMemoryBM25Retriever(document_store=self.store)

    @property
    def enabled(self) -> bool:
        return self.retriever is not None

    def candidates(self, query: str, *, limit: int = 12) -> set[tuple[str, int]]:
        if self.retriever is None:
            return set()
        result = self.retriever.run(query=query, top_k=max(1, min(limit, 50)))
        return {
            (str(document.meta.get("source_id")), int(document.meta.get("page")))
            for document in result.get("documents", [])
            if document.meta.get("source_id") and document.meta.get("page") is not None
        }
