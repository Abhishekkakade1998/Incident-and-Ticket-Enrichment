# PR: Incident & Ticket Enrichment Copilot – MCP servers, MCP client, document RAG, GUI

## Summary
Complete vertical slice for the Incident and Ticket Enrichment use case: Alarm Management API simulator (Postman contract), two MCP servers (alarm management, ticketing), copilot backend with MCP client + orchestration + grounded composer, hybrid RAG pipeline, and GUI with editable ticket drafts and approval-gated writes.

## Scope
- `simulators/`: Alarm API simulator (all Postman endpoints, auth, trace echo, pagination, fault injection) and mock ticketing API.
- `connectors/`: resilient HTTP connectors.
- `mcp-servers/`: 14 alarm tools + 5 ticketing tools.
- `rag/`: ingestion, retrieval, 10-document corpus.
- `apps/backend`, `apps/frontend`: copilot and GUI.
- Tests, Docker Compose, CI, docs.

## Architecture changes
New system; see `docs/architecture.md` and `docs/architecture-diagram.png`. The copilot reaches source systems only via MCP; MCP servers own the source credentials; writes require a human-minted, payload-bound approval token.

## MCP tools added
`search_assets`, `get_asset_metadata`, `list_alarms`, `get_alarm`, `get_alarm_summary`, `get_alarm_trends`, `correlate_alarms`, `analyze_alarm_floods`, `get_rationalization_candidates`, `score_alarm_priority`, `rank_alarms_by_priority`, `get_operator_recommendations`, `compute_alarm_kpi`, `list_kpi_definitions`, `search_similar_tickets`, `list_tickets`, `get_ticket`, `create_ticket` (write), `add_ticket_comment` (write). Catalog: `docs/mcp-tool-catalog.md`.

## RAG workflow added
Extract (.md/.txt/.html/.json) -> sanitise -> heading-aware chunking -> hashing embeddings -> JSON index; hybrid BM25 + vector retrieval with RRF, coverage blend, metadata rerank, filters, trust boundary, confidence; citations `[D#]`. Used inside the incident, investigation, similar-ticket, procedure and knowledge workflows.

## Screenshots
`docs/screenshots/01-incident-draft.jpg`, `02-mcp-trace.jpg`, `03-citations-and-approved-ticket.jpg`, `04-mcp-tool-discovery.jpg`, `05-degraded-partial-failure.jpg`.

## Test evidence
- `make test`: 163 passed (unit, MCP server, MCP client, RAG, orchestration, API, Postman contract, e2e on real ports).
- `make coverage`: 92 % line coverage.
- `make lint` and `make typecheck`: clean.

## Design decisions
`docs/design-decisions.md` (two MCP servers; deterministic workflows with dynamic MCP resolution; offline composer + citation-validated LLM; hashing-embedding hybrid RAG; HMAC approval + idempotency; uniform tool-call records for partial failure).

## Known limitations
`docs/known-limitations.md` (Docker not executed locally – CI builds and smoke-tests it; rule-based intents; lexical embeddings; no PDF ingestion; in-memory conversations; no GUI SSO).

## Review checklist
- [ ] Copilot never calls source APIs directly (only `copilot/mcp_client.py` talks to the outside)
- [ ] Every MCP tool has input + output schema, auth, retry/timeout, error mapping, trace
- [ ] RAG and MCP participate in the same workflow; citations visible in GUI
- [ ] Ticket creation impossible without explicit approval (`test_create_ticket_requires_valid_approval`, `test_approval_with_wrong_signing_key_is_blocked`)
- [ ] No secrets committed (`.env` ignored; gitleaks in CI)
- [ ] Tests, lint, types green in CI
