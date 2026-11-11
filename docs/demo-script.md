# Demo Video Script (about 9 minutes)

Record the screen with the stack running (`docker compose up --build` or `make run`). Pin the data for a reproducible run with `SIM_ANCHOR_TIME=2026-11-10T12:00:00Z` in `.env`.

| Time | Show | Say |
|---|---|---|
| 0:00-0:45 | README + architecture diagram | Use case, the two MCP servers, the RAG pipeline, and the rule that the copilot never calls source APIs directly. |
| 0:45-1:30 | GUI header (server status), **Tools** tab | MCP tool discovery: 19 tools on 2 servers, read-only vs WRITE badges, open an input schema and an output schema. |
| 1:30-3:30 | Send *"Prepare an incident for the highest-priority active alarm in EastRefinery."* | Walk the answer: selected K-501 surge out of 5 active alarms, correlated M-501 overcurrent, ranked causes with sources, recommended actions, TG-CMP-012 procedure. Click a `[D1]` chip (Citations tab) and a `[T2]` chip (MCP trace). |
| 3:30-4:30 | **MCP trace** tab | Plan step; each tool call's arguments, `uses output of` dependency, status, API status codes, duration; the two RAG steps with filters and hits. Point out the trace_id that also appears in the source API calls. |
| 4:30-5:30 | Follow-ups: *similar tickets*, *show open tickets linked to correlated assets*, *add the applicable troubleshooting procedure* | Context retention; the draft gets the procedure section and the linked document. |
| 5:30-6:30 | **Ticket draft** tab -> edit the title -> *Save edits* -> *Approve & create ticket* -> confirmation dialog | Explicit approval; the token is bound to the exact payload; INC id returned; **Audit** tab shows approval_granted / ticket_created. Typing *"Create the ticket"* in chat only asks for confirmation. |
| 6:30-7:30 | Acceptance scenario prompt (Boiler Feed Pump 101, 90 days) | Asset resolution via MCP, summary/trend/correlation chain, cavitation pattern, SOP-PUMP-004 steps, similar INC-1001/1003. |
| 7:30-8:30 | **Degraded scenario**: inject a fault, then re-ask the acceptance prompt; then clear the fault (commands below) | Retries, then `upstream_unavailable`; response marked **partial**, still answered and cited; failed step shown in red in the trace with 3 attempts. Mention the poisoned document warning ("chunks excluded"). |
| 8:30-9:00 | Terminal: `make test`, coverage line | 163 tests incl. real-port e2e; 92 % coverage; CI workflow. |

Inject the fault (persistent 503 on the correlation endpoint):

```bash
curl -X POST localhost:8000/_admin/faults -H "Authorization: Bearer $ALARM_API_TOKEN" -H "Content-Type: application/json" -d '{"path_pattern":"^/alarms/correlation$","mode":"error","status_code":503,"remaining":100}'
```

Clear it afterwards:

```bash
curl -X DELETE localhost:8000/_admin/faults -H "Authorization: Bearer $ALARM_API_TOKEN"
```
