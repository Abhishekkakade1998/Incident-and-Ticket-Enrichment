# RAG Design

## Source document types

`rag/documents/` holds a synthetic but realistic corpus (10 documents, ~48 chunks) matching the use case:

| doc_id | Type | Format | Content |
|---|---|---|---|
| SOP-PUMP-004 | operating_procedure | .md | Boiler feed pump alarm response (per-alarm steps, cavitation sequence) |
| TG-CMP-012 | troubleshooting_guide | .md | Compressor surge / discharge temperature troubleshooting |
| TG-MTR-007 | troubleshooting_guide | .md | Motor overcurrent / winding temperature |
| KA-0042 | knowledge_article | .md | Recognising cavitation from alarm patterns |
| KA-0057 | knowledge_article | .md | Anti-surge valve stroke test |
| ESC-001 | escalation_procedure | .md | Incident priority definitions, required ticket content, assignment groups |
| ALM-PHIL-001 | alarm_philosophy | .txt | Alarm priorities, chattering, floods, ack targets |
| SAF-003 | safety_instruction | .html | Hot feedwater isolation |
| RN-2026 | resolution_notes | .json | Historical ticket resolution notes (one record per section) |
| KA-0099 | knowledge_article (unverified) | .md | **Deliberately poisoned** forum import used to test prompt-injection defences |

## Ingestion flow

`python -m rag.ingestion.ingest --docs rag/documents --index rag/index/index.json` (also `make ingest`; runs automatically as the `rag-ingest` compose job and on backend start if the index is missing).

```
discover -> extract (per format) -> validate metadata -> sanitise -> chunk -> embed -> atomic write
```

## Text extraction

* **Markdown / text:** YAML-style front matter for metadata; body split on headings.
* **HTML:** stdlib `HTMLParser`; `<meta name=...>` tags become metadata; `<script>`, `<style>`, `<template>` dropped; `h1-h4` kept as structure; list items kept as list lines.
* **JSON:** document-level metadata plus `records[]`, each record becomes a section; record-level `asset_types` / `alarm_names` are merged up for filtering.
* Required metadata: `doc_id`, `title`, `doc_type`. Missing fields or duplicate `doc_id`s are reported and the document is skipped (the run exits non-zero).
* PDFs are not supported in this slice (see known limitations).

## Chunking strategy

* **Structure-aware:** one heading section at a time, keeping the `H2 > H3` path as `section` so citations point at a findable place.
* **Windowing:** sections longer than 160 words are split on paragraph / list-item boundaries with 30 words of overlap; a single oversized paragraph is hard-split by words.
* **List preservation:** numbered steps stay one per line, so procedure steps can be extracted and attached to tickets.
* Chunks shorter than 4 words are dropped.
* The embedded text is `title. section. text`, so short steps keep their context.

## Chunk metadata

`chunk_id` (`DOC#NNN`), `doc_id`, `title`, `doc_type`, `section`, `text`, `source_path`, `version`, `effective_date`, `trust` (`verified` / `unverified`), `asset_types`, `alarm_names`, `sites`, `position`, `word_count`, `checksum`, `injection_flags`.

## Embedding model / retrieval method

* **Default embedder `hashing-v1`:** deterministic signed feature hashing (512 dims) of stemmed unigrams, bigrams and character 4-grams plus a small domain synonym map (npsh <-> suction/cavitation, sop <-> procedure, ...), L2-normalised. No model download, so ingestion and tests are reproducible offline and in CI.
* The `Embedder` protocol (`rag/embeddings.py`) is the swap point for a neural model (sentence-transformers, a hosted embedding API). Choosing another embedder (`--embedder`) changes the index's `embedder` field; vectors are re-computed automatically.

## Vector database / index

* `rag/index/index.json`: chunks, vectors (5-decimal precision), per-document checksums, embedder name and dimension, build time. Written atomically (temp file + `os.replace`).
* BM25 statistics are derived at load time.
* `VectorIndex` is the seam for pgvector / Qdrant / OpenSearch; at this corpus size an in-process index keeps the stack to fewer containers.

## Hybrid search, ranking and reranking

1. **Hard filters** remove chunks: `doc_types`, `sites`, and with `strict_asset_type` any chunk scoped to *other* asset types (generic documents with no `asset_types` remain).
2. **BM25** (k1 = 1.4, b = 0.75) over `section + text` and **cosine** similarity over the vectors.
3. **Reciprocal Rank Fusion** (k = 60) of both rankings, normalised to [0, 1].
4. **Absolute evidence blend:** `0.4 * fused + 0.2 * bm25_norm + 0.4 * query_term_coverage`. Coverage stops a weak "best" match for an off-topic query from looking relevant.
5. **Metadata rerank (soft context):** +0.08 matching `asset_type`, +0.10 matching `alarm_names`, +0.12 when the section heading names the alarm.
6. **Diversification:** at most 2 chunks per document in the top-k (default k = 5).

## Retrieval filters used by the copilot

| Workflow | Filters |
|---|---|
| Troubleshooting guidance for an alarm | `asset_type` (strict), `alarm_names` (soft), `sites` |
| Escalation rules | `doc_types=[escalation_procedure]` |
| Similar-ticket context | `doc_types=[resolution_notes, knowledge_article]`, `asset_type`, `alarm_names` |
| Add procedure to draft | `doc_types=[operating_procedure, troubleshooting_guide]`, `asset_type` (strict), `alarm_names` |
| General knowledge question | `asset_type` from the question or the conversation, `sites` |

## Citation construction

* Each hit becomes a `Citation` with `ref` (`[D1]`...), `doc_id`, `title`, `section`, `chunk_id`, `doc_type`, `version`, `source_path`, display score and a snippet (whole chunk up to 320 chars, cut at a word).
* Refs are numbered per response across every retrieval in the workflow and de-duplicated by `chunk_id`.
* The GUI renders `[D#]` markers as chips that jump to the citation card. Draft tickets list linked documents (`doc_id` + section).
* Examples: `test-data/example_retrievals.json` (hits, scores, boosts, metadata, citations for five queries) and `test-data/example_response_acceptance_scenario.json`.

## Low-confidence handling

* `confidence`: **high** when the top hit covers >= 50 % of the query terms and scores >= 0.7; **medium** at >= 34 % coverage and >= 0.5; otherwise **low**; **none** if nothing passes `min_score` (0.3).
* Knowledge questions with low confidence return "I won't guess" plus the closest sources. Incident answers add "Documentation coverage is low" and lean on source-system data. `add_procedure` refuses to modify the draft and says why. If the chosen section does not name the alarm, the answer says the guidance is the closest match, not an exact one.

## Prompt-injection protections

1. **Ingestion screening** (`rag/ingestion/sanitize.py`): sentences that match instruction-override, role-hijack, secret-exfiltration, approval-bypass or safety-bypass patterns are replaced with `[content removed: instruction-like text in source document]` and the chunk records `injection_flags`.
2. **Retrieval trust boundary:** flagged chunks and `trust: unverified` sources are excluded by default and listed in `excluded_chunk_ids`; the GUI shows a warning. Clean chunks from unverified sources can be included only with an explicit `include_unverified`.
3. **Prompt fencing (LLM mode):** documents are passed inside `<document ref=...>` tags with a system instruction that they are untrusted and must never be followed.
4. **Output validation:** an LLM answer is rejected if it uses any citation marker not present in the evidence bundle, or none at all.
5. **Capability isolation:** documents cannot trigger tools. The orchestrator's tool chain is code-defined, and the only write tool needs a human-minted, payload-bound approval token.
6. **Output encoding:** the GUI HTML-escapes all server text before its minimal Markdown transform; the CSP forbids inline scripts.

Tested in `rag/tests/test_ingestion.py::test_poisoned_document_chunks_are_flagged`, `rag/tests/test_retrieval.py::test_injected_and_unverified_chunks_are_excluded`, `tests/integration/test_orchestration.py::test_injected_document_never_reaches_answer` and `tests/integration/test_llm_grounding.py`.

## Index refresh process

* Re-run ingestion (`make ingest` or restart the `rag-ingest` compose job). The run is **incremental**: per-document checksums are compared, and vectors for unchanged chunks (`chunk_id` + chunk checksum) are reused.
* The backend loads the index at start-up; restart `copilot-backend` (or redeploy) to pick up a new index. Hot reload is a listed future improvement.
* Embedder changes force full re-embedding because the stored embedder name / dimension no longer match.
