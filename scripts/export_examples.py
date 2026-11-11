"""Regenerate test-data/ examples: retrieved chunks, citations and a full copilot response.

Usage: PYTHONPATH=.:apps/backend:mcp-servers/alarm-management:mcp-servers/ticketing python scripts/export_examples.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "apps/backend"), str(ROOT / "mcp-servers/alarm-management"), str(ROOT / "mcp-servers/ticketing")]

import httpx  # noqa: E402

from tests import conftest as c  # noqa: E402

OUT = ROOT / "test-data"


async def main() -> None:
    OUT.mkdir(exist_ok=True)
    index = OUT / ".example-index.json"
    c.ingest(ROOT / "rag" / "documents", index)
    retriever = c.Retriever.from_path(index)
    examples = []
    from rag.retrieval.retriever import RetrievalFilters as F

    for q, f in [
        (
            "Low Suction Pressure High Vibration Boiler Feed Pump 101 troubleshooting procedure",
            F(asset_type="pump", alarm_names=["Low Suction Pressure", "High Vibration"], strict_asset_type=True),
        ),
        ("Surge Detected Gas Compressor K-501 immediate response", F(asset_type="compressor", alarm_names=["Surge Detected"], strict_asset_type=True)),
        ("incident priority definitions escalation response notify", F(doc_types=["escalation_procedure"])),
        ("bearing temperature tips overgreasing", F(asset_type="pump")),
        ("what is on the cafeteria menu", F()),
    ]:
        res = retriever.search(q, f)
        examples.append(
            {
                "query": q,
                "filters": res.filters,
                "confidence": res.confidence,
                "warnings": res.warnings,
                "excluded_chunk_ids": res.excluded_chunk_ids,
                "hits": [
                    {
                        "chunk_id": h.chunk.chunk_id,
                        "doc_id": h.chunk.doc_id,
                        "section": h.chunk.section,
                        "score": h.score,
                        "bm25": h.bm25,
                        "vector": h.vector,
                        "coverage": h.coverage,
                        "boosts": h.boosts,
                        "metadata": {k: getattr(h.chunk, k) for k in ("doc_type", "version", "asset_types", "alarm_names", "trust", "source_path")},
                    }
                    for h in res.hits
                ],
                "citations": [x.model_dump() for x in res.citations()],
            }
        )
    (OUT / "example_retrievals.json").write_text(json.dumps(examples, indent=2))
    index.unlink()

    sim = c.create_alarm_sim(token="demo-token", anchor=c.ANCHOR)
    ts = c.create_ticket_sim(token="ticket-demo-token")
    ac = c.AlarmApiClient("http://a", "demo-token", transport=httpx.ASGITransport(app=sim))
    tc = c.TicketingClient("http://t", "ticket-demo-token", transport=httpx.ASGITransport(app=ts))
    reg = c.ToolRegistry(
        [
            c.InMemoryMcpConnection("alarm-management", c.build_alarm_server(client=ac)),
            c.InMemoryMcpConnection("ticketing", c.build_ticket_server(c.TicketingMcpSettings(approval_signing_key="k"), client=tc)),
        ]
    )
    await reg.discover()
    orch = c.Orchestrator(reg, retriever, c.OfflineProvider(), c.ConversationStore(), c.AuditLog(":memory:"))
    q = (
        "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely contributing factors, "
        "retrieve the relevant operating procedure, and provide recommended actions with source evidence."
    )
    r1 = await orch.handle(q, None)
    r2 = await orch.handle("Prepare an incident for the highest-priority active alarm in EastRefinery.", None)
    (OUT / "example_response_acceptance_scenario.json").write_text(json.dumps(r1, indent=2, default=str))
    (OUT / "example_response_prepare_incident.json").write_text(json.dumps(r2, indent=2, default=str))
    (OUT / "example_answer_acceptance_scenario.md").write_text(r1["answer_markdown"])
    (OUT / "mcp_tool_catalog.json").write_text(json.dumps([t.as_dict() for t in reg.tools.values()], indent=2))
    print("wrote", sorted(p.name for p in OUT.iterdir()))


asyncio.run(main())
