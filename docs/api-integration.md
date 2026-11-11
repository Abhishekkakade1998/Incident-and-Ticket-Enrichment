# API Integration

## Alarm Management API simulator (`simulators/alarm_api_sim`)

Built from the reference Postman collections in `postman/`. `tests/integration/test_simulator_contract.py` runs **every request in `Alarm-API-Simulator.postman_collection.json` and all ten chaining flows in `chaining/Alarm-API-Chaining.postman_collection.json`** against the simulator, substitutes and extracts chained variables as the collection scripts do, and asserts success and trace echo. You can also import the collections into Postman with `baseUrl=http://localhost:8000` and `auth_token` = your `ALARM_API_TOKEN`.

| Endpoint | Notes |
|---|---|
| `GET /health` | No auth |
| `GET /assets/search?query&site&unit&asset_type&limit&offset` | Ranked token match on name / tag / type / id |
| `GET /assets/{asset_id}/metadata` | Criticality, manufacturer, parent system, connected assets, maintenance, operating limits |
| `GET /alarms?asset_id&site&unit&status&severity&alarm_name&start_time&end_time&page&page_size&sort_by&sort_order` | `status` / `severity` comma-separated; `page_size` <= 200; `pagination{page,page_size,total,total_pages,has_next}` |
| `GET /alarms/{alarm_id}` | |
| `POST /alarms/summary` | `group_by` over alarm_name, asset_id, asset_name, severity, alarm_type, site, unit, status, priority; KPIs alarm_count, critical_count, recurring_rate, avg_ack_delay, avg_duration, suppression_candidate_rate, active_count |
| `POST /alarms/trends` | hourly / daily / weekly buckets |
| `POST /alarms/correlation` | co-occurrence within `lag_window_minutes`; candidate alarms include other assets in the same unit; returns `correlated_assets` |
| `POST /alarms/flood-analysis` | >= `threshold_count` alarms in a rolling window, merged windows |
| `POST /alarms/rationalization-candidates` | frequent, chattering, stale |
| `POST /alarms/priority-score` | 0-100 score + P1-P4 band from severity, type, asset criticality, 30-day recurrence, status |
| `POST /recommendations/operator-actions` | Actions, likely causes, procedure keywords, related alarms, asset context, 90-day pattern |
| `POST /calculation-code/generate` / `execute` | alarm_flood_index, critical_alarm_density, operator_response_efficiency, nuisance_alarm_score |
| `GET /analytics/kpi-definitions` | |
| `POST/DELETE /_admin/faults` | Test-only fault injection (5xx or latency per path regex) |

**Authentication:** `Authorization: Bearer <ALARM_API_TOKEN>` (default `demo-token`, as in the Postman collection). Missing -> 401, wrong -> 403.
**Trace headers:** `trace_id`, `x-client-id`, `x-metadata-tag`, `x-request-id` are echoed in response headers and in the body's `meta` block.
**Errors:** `{"error": {"code", "message", "details"}, "trace_id"}` with codes `validation_error` (422), `unauthorized`, `forbidden`, `not_found`, `injected_fault`.
**Data:** deterministic (seeded) synthetic dataset of 16 assets on three sites, about 7k alarm activations over the 300+ days before the anchor time (`SIM_ANCHOR_TIME`, default: the current hour). The seeded patterns (cavitation chain on BFP-101, K-501 surge with cross-asset M-501 overcurrent, Unit 2 floods, standing active alarms) are what the copilot is expected to discover.

## Mock ticketing API (`simulators/ticketing_api_sim`)

| Endpoint | Notes |
|---|---|
| `GET /tickets?asset_id=..&asset_id=..&status=open,in_progress&site&page&page_size` | Repeated `asset_id` params |
| `GET /tickets/{id}` | |
| `POST /tickets/search-similar` | TF-IDF cosine + boosts (same asset +0.25, same alarm +0.20, same asset type +0.08), returns `match_reasons` |
| `POST /tickets` | Requires `Idempotency-Key` header and an `approval` block; replaying a key returns the original ticket (`idempotent_replay: true`) |
| `POST /tickets/{id}/comments` | |

Seeded with 20 historical incidents (with resolution notes) and open tickets on BFP-102, K-501, M-501, K-502 and E-210.

## Connector layer (`connectors/`)

`JsonHttpClient` is the single HTTP path for both source systems:

* bearer auth injected per request; the token never appears in logs (`common.logging.redact`) or error messages;
* trace headers from the current `TraceContext`;
* `None` query parameters dropped, lists joined, path segments URL-encoded (prevents path traversal through IDs);
* retries on connect errors, timeouts, 429, 502, 503, 504 (exponential backoff + jitter, bounded);
* error taxonomy `AuthError`, `NotFoundError`, `ValidationFailed`, `RateLimited`, `UpstreamUnavailable`, `UpstreamTimeout`;
* `CallStats` (status, attempts, retries, duration) returned with every body and surfaced as `source_calls` in MCP tool outputs.

## Multi-step chaining implemented

| Copilot workflow | MCP chain (-> = output feeds input, \| = parallel) |
|---|---|
| Prepare incident | `list_alarms` -> `rank_alarms_by_priority` -> `get_alarm` -> (`get_asset_metadata` \| `score_alarm_priority` \| `get_operator_recommendations` \| `correlate_alarms`) -> (`search_similar_tickets` \| `list_tickets`) + RAG x2 |
| Investigate asset | `search_assets` -> `get_asset_metadata` -> (`get_alarm_summary` \| `get_alarm_trends` \| `correlate_alarms`) -> `list_alarms` -> (`get_operator_recommendations` \| `score_alarm_priority`) -> `search_similar_tickets` -> `list_tickets` + RAG x2 |
| Similar tickets | context alarm, or `search_assets` -> `get_asset_metadata` -> `list_alarms`, then `search_similar_tickets` + RAG |
| Linked tickets | `correlate_alarms` -> `list_tickets(asset + correlated assets)` |
| KPI | `compute_alarm_kpi` = generate -> execute inside the MCP server |
| Create ticket | GUI approval -> HMAC token -> `create_ticket` |
