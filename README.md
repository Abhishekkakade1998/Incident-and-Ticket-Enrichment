# Incident & Ticket Enrichment Copilot

[![CI](https://github.com/OWNER/senior-copilot-mcp-rag-assignment/actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

**Selected use case:** Incident and Ticket Enrichment Copilot (Senior Software Engineer – Copilot Integration assignment).

When a high-priority alarm fires, the copilot finds and ranks it, enriches it with asset context, correlations, recommended actions, similar historical tickets and the applicable procedure, then drafts an incident ticket. The operator edits the draft and explicitly approves it. All alarm and ticket data flows through **two candidate-developed MCP servers**. Procedures and knowledge come from a **document RAG pipeline** in the same workflow. Every statement in an answer is cited to a tool call `[T#]`, a document chunk `[D#]` or a ticket `[INC-#]`.

> **Demo video (up to 10 min):** _add link here_ · Script: [`docs/demo-script.md`](docs/demo-script.md) · Screenshots: [`docs/screenshots/`](docs/screenshots)

![Incident draft](docs/screenshots/01-incident-draft.jpg)

## Main capabilities

* Natural-language chat with intent detection and context retention across turns ("this alarm", "the draft", "correlated assets").
* **MCP tool discovery** across two servers (19 tools) with a tool-catalog view in the GUI.
* **Multi-step MCP chaining** where outputs feed the next call: asset search -> metadata -> summary / trends / correlation -> latest alarm -> priority / recommendations -> similar tickets / open tickets on correlated assets.
* **Hybrid RAG** (BM25 + vectors + metadata rerank) over SOPs, troubleshooting guides, knowledge articles, resolution notes and escalation rules, with filters, citations, low-confidence handling and prompt-injection defences.
* Evidence fusion: likely causes ranked by the number of agreeing sources, and alarm-system priority reconciled with the escalation procedure (conflicts surfaced).
* **Alarm-to-ticket draft** with editable fields, linked documents and similar tickets. **Write requires explicit approval**, enforced in the ticketing MCP server with an HMAC token bound to the exact payload, plus an idempotency key.
* **MCP execution trace** in the GUI: plan, each tool call (server, arguments, status, duration, API status codes, retries, dependencies), each retrieval (query, filters, hits, scores).
* Partial-failure handling (e.g. ticketing down -> `partial` answer still produced), structured errors, audit trail.

## Technology stack

Python 3.11+ · FastAPI · MCP Python SDK 1.x (FastMCP, Streamable HTTP) · httpx · Pydantic v2 · jsonschema · SQLite · vanilla JS GUI (no build) · optional Claude via the Anthropic SDK · pytest · ruff · mypy · Docker Compose · GitHub Actions.

## Repository layout

```
apps/backend/copilot/       copilot backend: API, orchestrator, intent, composer, MCP client, LLM provider, audit, state
apps/frontend/              GUI (index.html, app.js, styles.css) + nginx Dockerfile
mcp-servers/alarm-management/alarm_mcp/   Alarm Management MCP server (14 tools)
mcp-servers/ticketing/ticketing_mcp/      Ticketing MCP server (5 tools; secondary source)
connectors/                 reusable HTTP connectors (auth, trace, retries, error mapping)
simulators/                 Alarm Management API simulator + mock ticketing API
rag/                        ingestion/, retrieval/, documents/ (corpus), tests/
common/                     logging (redaction), tracing, MCP helpers, approval tokens
tests/                      unit/, integration/, e2e/
postman/                    reference API contract (collections from the assignment)
test-data/                  example retrievals, citations, full copilot responses, tool catalog JSON
docs/                       architecture, diagram, tool catalog, RAG design, API integration, decisions, limitations
scripts/                    run_local.sh, gen_tool_catalog.py, export_examples.py, draw_architecture.py
```

## Quick start

### Option A – Docker Compose (preferred)

```bash
cp .env.example .env
```

Then replace every `replace-me` in `.env` (any random strings work locally; `ALARM_API_TOKEN` is the token the Postman collection calls `auth_token`).

```bash
docker compose up --build
```

Open http://localhost:8080. Compose starts `alarm-api` (:8000), `ticketing-api` (:8100), `alarm-mcp` (:9001), `ticketing-mcp` (:9002), the one-shot `rag-ingest` job, `copilot-backend` (:8081) and `gui` (:8080, nginx proxying `/api`). Health checks and `depends_on` conditions start them in order.

### Option B – local Python (no Docker)

```bash
make install
```

```bash
make run
```

`make run` runs `scripts/run_local.sh`, which starts all five services and serves the GUI from the backend at http://127.0.0.1:8080.

## Run the MCP servers independently

Each MCP server is a standalone process (Streamable HTTP at `/mcp`, or stdio). It needs only its source system and its env vars:

```bash
PYTHONPATH=. python -m uvicorn simulators.alarm_api_sim.app:app --port 8000
```

```bash
PYTHONPATH=.:mcp-servers/alarm-management ALARM_API_BASE_URL=http://127.0.0.1:8000 ALARM_API_TOKEN=demo-token ALARM_MCP_TOKEN=secret python -m alarm_mcp
```

```bash
PYTHONPATH=.:mcp-servers/alarm-management ALARM_API_TOKEN=demo-token python -m alarm_mcp --transport stdio
```

The first command starts the alarm API the server depends on. The second serves the MCP server on http://127.0.0.1:9001/mcp (bearer `secret`). The third runs it over stdio, e.g. for Claude Desktop or the MCP Inspector. The ticketing server works the same way (`python -m ticketing_mcp`, port 9002, `TICKETING_API_URL`, `TICKETING_API_TOKEN`, `TICKETING_MCP_TOKEN`, `APPROVAL_SIGNING_KEY`). To inspect the tools interactively:

```bash
npx @modelcontextprotocol/inspector
```

Connect it to `http://127.0.0.1:9001/mcp` with header `Authorization: Bearer secret`.

## MCP server and tools

| Server | Tools |
|---|---|
| `alarm-management` (read-only) | `search_assets`, `get_asset_metadata`, `list_alarms` (pagination, `fetch_all_pages`), `get_alarm`, `get_alarm_summary`, `get_alarm_trends`, `correlate_alarms`, `analyze_alarm_floods`, `get_rationalization_candidates`, `score_alarm_priority`, `rank_alarms_by_priority`, `get_operator_recommendations`, `compute_alarm_kpi`, `list_kpi_definitions` |
| `ticketing` | `search_similar_tickets`, `list_tickets`, `get_ticket`, **`create_ticket`** (write, approval token + idempotency key), **`add_ticket_comment`** (write, approval token) |

Engineering features: typed Pydantic input **and** output schemas (published `outputSchema`, output validated), input validation (patterns, ranges, enums, scope checks), env-based config, bearer auth on the MCP endpoint, source-token injection, trace metadata (`_meta` / headers -> source API -> output `trace_id`), pagination, per-call and per-tool timeouts, bounded retries with backoff, API error mapping to stable codes, structured logs with secret redaction. Full per-tool documentation (schemas, source operation, auth, errors, timeouts, example invocation and response) is in [`docs/mcp-tool-catalog.md`](docs/mcp-tool-catalog.md), generated from the live servers.

## RAG corpus and ingestion

Ten documents in [`rag/documents/`](rag/documents) (Markdown, text, HTML, JSON): pump and compressor procedures, a motor troubleshooting guide, knowledge articles, a resolution-notes digest, escalation rules, an alarm-philosophy excerpt, a safety instruction, and one deliberately poisoned "forum tips" file used to test injection handling.

```bash
make ingest
```

`make ingest` runs `python -m rag.ingestion.ingest --docs rag/documents --index rag/index/index.json`. The index is built incrementally: unchanged documents keep their vectors. Pipeline: extract -> validate metadata -> sanitise prompt-injection text -> heading-aware chunking (160 words, 30 overlap) -> hashing embeddings -> JSON index. Retrieval: hard filters -> BM25 + cosine -> RRF -> coverage blend -> metadata rerank -> per-document diversity -> confidence. Details in [`docs/rag-design.md`](docs/rag-design.md); example chunks and citations in [`test-data/example_retrievals.json`](test-data/example_retrievals.json).

## Configuration

All configuration is via environment variables; see [`.env.example`](.env.example). Key ones:

| Variable | Purpose |
|---|---|
| `ALARM_API_BASE_URL`, `ALARM_API_TOKEN` | Alarm Management API (used **only** by the alarm MCP server) |
| `TICKETING_API_URL`, `TICKETING_API_TOKEN` | Ticketing API (used only by the ticketing MCP server) |
| `ALARM_MCP_URL`, `ALARM_MCP_TOKEN`, `TICKETING_MCP_URL`, `TICKETING_MCP_TOKEN` | Copilot -> MCP connections and MCP endpoint auth |
| `APPROVAL_SIGNING_KEY`, `APPROVAL_TTL_S` | Human-approval tokens for writes (shared by backend + ticketing MCP) |
| `SOURCE_TIMEOUT_S`, `SOURCE_MAX_RETRIES`, `TOOL_TIMEOUT_S`, `MCP_TOOL_TIMEOUT_S` | Timeouts and retries |
| `DOCUMENT_PATH`, `RAG_INDEX_PATH`, `RAG_AUTO_INGEST`, `RAG_TOP_K` | RAG |
| `LLM_PROVIDER` (`offline` / `anthropic`), `LLM_API_KEY`, `LLM_MODEL` (default `claude-opus-5-5`) | Replaceable LLM provider |
| `SIM_ANCHOR_TIME` | Pin the simulator's "now" for reproducible demos |
| `GUI_API_BASE_URL`, `GUI_DEFAULT_USER`, `COPILOT_BACKEND_URL` | GUI container runtime config |

## Build, run and test commands

```bash
make test
```

`make test` runs all 163 tests: unit, MCP server, MCP client, RAG, orchestration, API, and e2e on real ports. Narrower targets:

| Command | What it runs |
|---|---|
| `make test-unit` | Unit tests |
| `make test-mcp` | MCP server and MCP client tests |
| `make test-rag` | RAG ingestion and retrieval tests |
| `make test-e2e` | End-to-end test with every service on real ports |
| `make coverage` | Tests with coverage (92 % line coverage; HTML report in `docs/coverage/html`, XML in `docs/coverage/coverage.xml`) |
| `make lint` | ruff lint (incl. security rules) + format check |
| `make typecheck` | mypy |
| `make audit` | pip-audit dependency scan |

## Sample interactions

| Prompt | What happens |
|---|---|
| *Prepare an incident for the highest-priority active alarm in EastRefinery.* | `list_alarms` (active, all pages) -> `rank_alarms_by_priority` -> `get_alarm` -> metadata / priority / recommendations / correlation -> similar + open tickets -> RAG (TG-CMP-012, ESC-001). Selects **K-501 Surge Detected (P1)**, links INC-1008/1010/1011 and the correlated motor M-501's open INC-1012, and creates an editable draft. |
| *Find similar historical tickets for this compressor alarm.* | Uses the alarm from context; similar tickets with resolutions + root causes from resolution notes. |
| *Summarize the issue, likely cause, affected asset, and recommended action.* | Refreshes the alarm, recommendations and procedure; concise cited summary. |
| *Add the applicable troubleshooting procedure to the ticket draft.* | Strict asset-type RAG for procedures; appends the best section not already in the draft and links the document. |
| *Show open tickets linked to correlated assets.* | `correlate_alarms` -> `list_tickets(asset + correlated assets, open)`. |
| *Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days ...* (acceptance scenario) | Finds the Low Suction Pressure -> High Vibration -> High Bearing Temperature cavitation chain, ranks causes (blocked strainer, cavitation, deaerator level, recirculation valve) with sources, cites SOP-PUMP-004 §3 steps and INC-1001/1003. See [`test-data/example_answer_acceptance_scenario.md`](test-data/example_answer_acceptance_scenario.md). |
| *Create the ticket* | Never writes; asks for approval in the GUI. |
| *What is the escalation procedure for a P1 incident?* | RAG-only cited answer from ESC-001. |
| *What is the cafeteria menu?* | Low confidence -> declines to guess. |

## Architecture summary

GUI -> copilot backend (API, orchestrator, composer, MCP client, retriever, optional LLM) -> **MCP servers** (bearer-protected, own source credentials) -> source APIs; RAG pipeline: documents -> ingestion -> index -> retriever, used inside the same workflows. See [`docs/architecture.md`](docs/architecture.md) and [`docs/architecture-diagram.png`](docs/architecture-diagram.png). Design rationale: [`docs/design-decisions.md`](docs/design-decisions.md). API contract and simulator: [`docs/api-integration.md`](docs/api-integration.md).

## Security

Secrets come only from the environment (`.env` is git-ignored) and are redacted from logs. The copilot holds no source-system credentials. MCP endpoints require bearer tokens. Tool inputs are validated twice (client JSON Schema, server Pydantic). The audit log uses parameterised SQL only. GUI output is HTML-escaped and protected by a strict CSP. Retrieved documents are untrusted: sanitised, excluded when flagged or unverified, fenced in prompts, and LLM output is citation-validated. Writes need a human-minted, payload-bound, expiring approval token, verified by the ticketing MCP server, plus an idempotency key. CI runs ruff security rules (bandit set), pip-audit and gitleaks.

## Assumptions

* The Postman collections are the API contract; behaviour they leave unspecified (e.g. correlation scope, KPI formulas, priority scoring) is defined in [`docs/api-integration.md`](docs/api-integration.md).
* "Active alarm" includes `active` and `acknowledged`-but-not-cleared activations.
* Correlation considers other assets in the same unit, so cross-asset effects (K-501 -> M-501) are discoverable.
* A mock ticketing API is an accepted ticketing option; the ticketing MCP contract is what a ServiceNow/Jira connector would implement.
* The default deterministic composer is an acceptable "grounded answer generation" when no LLM key is provided; Claude can be enabled with one variable.

## Known limitations

See [`docs/known-limitations.md`](docs/known-limitations.md). In short: Docker files were not executed on the development machine (CI builds and smoke-tests them), rule-based intents, lexical embeddings, no PDF ingestion, in-memory conversation state, no GUI SSO, and the demo video must be recorded by the submitter.
