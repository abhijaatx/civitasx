from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from civitas_api.research import DocumentIngestor, ResearchIndex, TranslationCache


def test_reference_corpus_has_three_authorities_and_thirty_documents():
    index = ResearchIndex(Path(__file__).parents[1] / "data" / "corpus" / "manifest.json")

    assert index.document_count == 30
    assert index.page_count >= 60
    assert set(index.authority_records) == {"gba", "bda", "bmrcl"}
    assert all(document.content_hash for document in index.documents.values())
    assert not index.hash_mismatches


def test_answer_is_extractive_and_keeps_page_citations():
    index = ResearchIndex(Path(__file__).parents[1] / "data" / "corpus" / "manifest.json")

    answer = index.answer("What is the Metro Phase 2A project cost?")

    assert answer.route == "transport"
    assert answer.generated_by == "extractive"
    assert answer.coverage.hits_returned > 0
    assert any(
        source.source_id == "bmrcl-phase2a-dpr" and source.page == 44
        for source in answer.sources
    )
    assert any("105,840,000,000" in calculation.result for calculation in answer.calculations)
    assert all(fact.claim_status == "supported" for fact in answer.facts)


def test_missing_evidence_is_explicit():
    index = ResearchIndex(Path(__file__).parents[1] / "data" / "corpus" / "manifest.json")

    answer = index.answer("Explain quantum computing")

    assert answer.sources == []
    assert answer.facts == []
    assert answer.coverage.missing
    assert answer.uncertainties
    assert "could not find" in answer.explanation.casefold()


def test_routing_questions_keep_multi_authority_matches_ambiguous():
    index = ResearchIndex(Path(__file__).parents[1] / "data" / "corpus" / "manifest.json")

    answer = index.answer("Which authority handles zoning and metro station access?")

    assert len(answer.authority_matches) > 1
    assert all(authority.status == "ambiguous" for authority in answer.authority_matches)


def test_kannada_passage_retains_original_and_translation(tmp_path: Path):
    index = ResearchIndex(Path(__file__).parents[1] / "data" / "corpus" / "manifest.json")

    answer = index.answer("pedestrian path improvement allocation")
    source = next(
        source for source in answer.sources if source.source_id == "gba-pedestrian-programme"
    )

    assert source.original_passage
    assert source.translation_language == "kn"
    assert "₹25 crore" in source.passage

    cache = TranslationCache(tmp_path / "translations.json")
    assert cache.translate(source.original_passage, "kn") == source.passage
    assert cache.translate(source.original_passage, "kn") == source.passage


def test_ingestor_preserves_pages_and_hashes(tmp_path: Path):
    raw = b"First page with a civic record.\fSecond page with a status."
    ingestor = DocumentIngestor(translation_cache=TranslationCache(tmp_path / "cache.json"))
    pages, method, extraction_status = ingestor.extract(raw, content_type="text/plain")
    entry = ingestor.manifest_entry(
        source_id="test-document",
        title="Test document",
        authority="Greater Bengaluru Authority",
        authority_id="gba",
        raw=raw,
        pages=pages,
        url="https://bbmp.gov.in/",
        status="adopted",
        extraction_method=method,
        extraction_status=extraction_status,
    )

    assert len(pages) == 2
    assert entry["content_hash"]
    assert entry["page_hash"]
    assert entry["extraction_status"] == "complete"
    raw_path = ingestor.write_bundle(tmp_path / "corpus", entry, raw, ".txt")
    assert raw_path.read_bytes() == raw
    assert (tmp_path / "corpus" / "documents" / "test-document.json").exists()


def test_document_comparison_aligns_pages_after_inserted_preface(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "authorities": [],
                "documents": [
                    {
                        "source_id": "baseline",
                        "title": "Baseline",
                        "authority": "GBA",
                        "authority_id": "gba",
                        "content_hash": "baseline-hash",
                        "pages": [
                            {"page": 1, "text": "Stable page one"},
                            {"page": 2, "text": "Stable page two"},
                        ],
                    },
                    {
                        "source_id": "current",
                        "title": "Current",
                        "authority": "GBA",
                        "authority_id": "gba",
                        "content_hash": "current-hash",
                        "pages": [
                            {"page": 1, "text": "New cover page"},
                            {"page": 2, "text": "Stable page one"},
                            {"page": 3, "text": "Stable page two"},
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    comparison = ResearchIndex(manifest).compare_documents("current", "baseline")
    assert [(change.page, change.change_type) for change in comparison.changes] == [(1, "added")]


@pytest.fixture()
def research_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CIVITAS_AUTH_MODE", "local")
    monkeypatch.setenv("CIVITAS_STORAGE", "sqlite")
    monkeypatch.setenv("CIVITAS_SQLITE_PATH", str(tmp_path / "civitas.sqlite3"))
    monkeypatch.setenv("CIVITAS_FRONTEND_ORIGIN", "http://localhost:5173")

    from civitas_api import config, main

    config.get_settings.cache_clear()
    main.get_store.cache_clear()
    main.get_research_index.cache_clear()
    main.get_live_registry.cache_clear()
    main.get_community_store.cache_clear()
    main.get_agent.cache_clear()
    with TestClient(main.app) as client:
        yield client
    main.get_store.cache_clear()
    main.get_research_index.cache_clear()
    main.get_live_registry.cache_clear()
    main.get_community_store.cache_clear()
    main.get_agent.cache_clear()
    config.get_settings.cache_clear()


def _register(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/auth/register",
        json={
            "name": "Research Resident",
            "email": email,
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def test_research_endpoint_persists_case_answer_and_is_owner_scoped(research_client: TestClient):
    first = _register(research_client, "research-first@example.com")
    second = _register(research_client, "research-second@example.com")
    first_headers = {"Authorization": f"Bearer {first}"}
    second_headers = {"Authorization": f"Bearer {second}"}
    case = research_client.post(
        "/api/cases",
        headers=first_headers,
        json={"goal": "Understand the Bengaluru budget allocation for footpaths"},
    ).json()

    result = research_client.post(
        "/api/research",
        headers=first_headers,
        json={"question": case["goal"], "case_id": case["id"]},
    )
    assert result.status_code == 200, result.text
    assert result.json()["coverage"]["hits_returned"] > 0
    history = research_client.get(f"/api/cases/{case['id']}/research", headers=first_headers)
    assert history.status_code == 200
    assert len(history.json()["items"]) == 1
    assert (
        research_client.get(
            f"/api/cases/{case['id']}/research", headers=second_headers
        ).status_code
        == 404
    )
