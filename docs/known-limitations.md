# Known Limitations and Future Improvements

## Known limitations

* **Docker packaging not executed on the development machine.** The Dockerfiles and `docker-compose.yml` are written and the CI `build` job runs `docker compose build` plus a smoke test, but Docker was not available locally while building this. Everything was verified with the equivalent non-Docker runner (`scripts/run_local.sh`) and the real-port e2e tests.
* **Intent detection is rule-based.** Phrasings far outside the supported intents fall back to a documentation answer or a help message. Multi-intent sentences take the strongest intent.
* **Offline answers are templated.** Natural prose needs `LLM_PROVIDER=anthropic` and an API key.
* **Embeddings are lexical-hashing based**, not neural. Paraphrases with no shared terms can be missed (BM25 + synonyms mitigate this).
* **No PDF / DOCX extraction**; the corpus formats are Markdown, text, HTML and JSON.
* **Conversation state and drafts are in memory** (bounded LRU with TTL), so they are lost on backend restart and not shared across replicas. The audit log is persistent.
* **GUI authentication is out of scope:** the operator name comes from the approval dialog and the `x-user` header. Production would use SSO (OIDC) and derive `approved_by` from the token, with role-based permission to approve P1 tickets.
* **A new MCP session per tool call** (stateless servers) adds about 10-50 ms per call.
* **Prompt-injection screening is pattern-based**; novel phrasings could pass ingestion. The trust boundary, prompt fencing, citation validation and the approval gate are the other layers.
* **Simulated data**: the alarm dataset is synthetic and seeded; the ticketing system is a mock, not Jira / ServiceNow.
* **Demo video:** the repository includes screenshots and a demo script (`docs/demo-script.md`). The recording itself has to be made and linked by the submitter.

## Future improvements

1. LLM planner that selects tools from the discovered catalog (schemas already exposed), with the current workflows as guarded templates.
2. Neural embeddings (e.g. a sentence-transformer or hosted embedding API) and a vector DB (pgvector / Qdrant), plus a cross-encoder reranker.
3. Real ticketing connector (ServiceNow / Jira) behind the same ticketing MCP contract; ticket update (`add_ticket_comment` is already approval-gated) from the GUI.
4. OpenTelemetry traces / metrics export (the trace IDs and per-step timings are already propagated end to end).
5. Redis-backed conversations and drafts; MCP session pooling.
6. OIDC for the GUI, per-role approval policies, approval-signing keys in a KMS with rotation.
7. Index hot-reload and a document-upload ingestion endpoint with review / approval for new sources.
8. Retrieval evaluation set (queries with expected chunks) tracked in CI with recall@k / MRR.
