# Bengaluru evidence corpus

`manifest.json` is the Phase 2 deterministic seed: 30 official-reference
records, ten each for the Greater Bengaluru Authority (GBA), Bangalore
Development Authority (BDA), and Bangalore Metro Rail Corporation Limited
(BMRCL). Each record keeps an authority URL, status, publication and retrieval
dates, page passages, a raw-file hash, and a page-content hash. The short text
is a demo-safe reference snapshot; it is not a substitute for the complete
original PDF.

The `raw/` directory holds the matching text snapshots used by local tests.
For an actual source, download the original through an approved source route,
confirm its metadata, and run `scripts/ingest_corpus.py`. That command stores
the original bytes and extracted pages so a refresh worker can later upload
versioned bundles to the private corpus bucket.
