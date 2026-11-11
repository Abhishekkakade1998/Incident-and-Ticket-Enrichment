"""Copilot orchestration: intent -> plan -> MCP tool chain + RAG -> grounded answer.

Each workflow is a short, explicit chain of reusable steps. Outputs of one MCP tool are
passed into the next (asset search -> asset id -> alarms -> alarm id -> priority /
recommendations -> similar tickets ...). Every step is recorded in the execution trace;
failures of optional steps downgrade the response to ``partial`` instead of aborting.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from common.logging import log_event
from common.tracing import current_trace
from copilot import compose
from copilot.audit import AuditLog
from copilot.intent import ParsedRequest, classify
from copilot.llm import LLMProvider
from copilot.mcp_client import ToolCallRecord, ToolRegistry
from copilot.state import Conversation, ConversationStore, TicketDraft, TicketFields
from rag.models import Citation, RetrievalResult
from rag.retrieval.retriever import RetrievalFilters, Retriever

log = logging.getLogger("copilot.orchestrator")

OPEN_STATUSES = ["open", "in_progress"]


@dataclass
class Run:
    """Per-request working state: trace, evidence, warnings."""

    orch: Orchestrator
    conv: Conversation
    parsed: ParsedRequest
    steps: list[dict[str, Any]] = field(default_factory=list)
    calls: list[ToolCallRecord] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = "ok"
    ev: compose.Evidence = field(default_factory=lambda: compose.Evidence(intent="", question=""))

    def __post_init__(self) -> None:
        self.ev = compose.Evidence(intent=self.parsed.intent, question=self.parsed.text)
        self.ev.lookback_days = self.parsed.entities.lookback_days or self.orch.default_lookback_days

    # ---------------------------------------------------------------- MCP
    async def tool(self, name: str, args: dict[str, Any], purpose: str, depends_on: list[str] | None = None) -> tuple[dict[str, Any] | None, str | None]:
        rec = await self.orch.registry.call(name, args, purpose=purpose, depends_on=depends_on)
        self.calls.append(rec)
        step = rec.as_trace()
        ref = None
        if rec.ok:
            ref = f"[T{sum(1 for c in self.calls if c.ok)}]"
            self.ev.tool_refs[rec.id] = ref
        step["ref"] = ref
        self.steps.append(step)
        self.orch.audit.record(
            "mcp_tool_call",
            actor="copilot",
            conversation_id=self.conv.conversation_id,
            trace_id=_tid(),
            tool=name,
            server=rec.server,
            status=rec.status,
            duration_ms=round(rec.duration_ms, 1),
            error=rec.error,
        )
        if not rec.ok:
            err = rec.error or {}
            self.warnings.append(f"{name} ({rec.status}): {err.get('message', 'failed')}")
            self.status = "partial"
            return None, None
        return rec.output, ref

    def call_id(self, ref: str | None) -> list[str]:
        if not ref:
            return []
        return [cid for cid, rf in self.ev.tool_refs.items() if rf == ref]

    # ---------------------------------------------------------------- RAG
    def rag(self, query: str, filters: RetrievalFilters, purpose: str, k: int | None = None) -> RetrievalResult | None:
        started = time.perf_counter()
        if self.orch.retriever is None:
            self.warnings.append("Document index unavailable; answering from source-system data only")
            self.steps.append({"kind": "rag", "purpose": purpose, "status": "unavailable", "query": query, "duration_ms": 0})
            self.status = "partial"
            return None
        res = self.orch.retriever.search(query, filters, k=k or self.orch.top_k)
        new = self._add_citations(res)
        self.steps.append(
            {
                "id": f"R-{len(self.steps) + 1}",
                "kind": "rag",
                "purpose": purpose,
                "status": "ok",
                "query": query,
                "filters": res.filters,
                "confidence": res.confidence,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "hits": [{"chunk_id": h.chunk.chunk_id, "doc_id": h.chunk.doc_id, "score": h.score, "ref": c.ref} for h, c in zip(res.hits, new, strict=False)],
                "excluded": res.excluded_chunk_ids,
                "warnings": res.warnings,
            }
        )
        self.orch.audit.record(
            "rag_retrieval",
            actor="copilot",
            conversation_id=self.conv.conversation_id,
            trace_id=_tid(),
            query=query[:300],
            confidence=res.confidence,
            chunks=[h.chunk.chunk_id for h in res.hits],
            excluded=res.excluded_chunk_ids,
        )
        for w in res.warnings:
            if "excluded" in w:
                self.warnings.append(w)
        return res

    def _add_citations(self, res: RetrievalResult) -> list[Citation]:
        out = []
        known = {c.chunk_id: c for c in self.citations}
        for c in res.citations():
            if c.chunk_id in known:
                out.append(known[c.chunk_id])
                continue
            c.ref = f"[D{len(self.citations) + 1}]"
            self.citations.append(c)
            known[c.chunk_id] = c
            out.append(c)
        self.ev.citations = self.citations
        return out

    def plan(self, steps: list[str]) -> None:
        self.steps.insert(
            0,
            {
                "id": "P-0",
                "kind": "plan",
                "intent": self.parsed.intent,
                "confidence": self.parsed.confidence,
                "entities": self.parsed.entities.as_dict(),
                "planned_steps": steps,
                "status": "ok",
                "duration_ms": 0,
            },
        )


def _tid() -> str | None:
    t = current_trace()
    return t.trace_id if t else None


class Orchestrator:
    def __init__(
        self,
        registry: ToolRegistry,
        retriever: Retriever | None,
        llm: LLMProvider,
        store: ConversationStore,
        audit: AuditLog,
        *,
        default_lookback_days: int = 90,
        top_k: int = 5,
    ) -> None:
        self.registry = registry
        self.retriever = retriever
        self.llm = llm
        self.store = store
        self.audit = audit
        self.default_lookback_days = default_lookback_days
        self.top_k = top_k

    # ================================================================ entry point
    async def handle(self, message: str, conversation_id: str | None, user: str = "operator") -> dict[str, Any]:
        started = time.perf_counter()
        conv = self.store.get_or_create(conversation_id)
        parsed = classify(message)
        run = Run(self, conv, parsed)
        conv.remember("user", message)
        self.audit.record("chat_request", actor=user, conversation_id=conv.conversation_id, trace_id=_tid(), intent=parsed.intent, message=message[:500])
        if not self.registry.tools:
            await self.registry.discover()
        handler = getattr(self, f"_wf_{parsed.intent}")
        try:
            result = await handler(run)
        except Exception as exc:
            log.exception("workflow failed")
            run.status = "error"
            result = {
                "heading": "Something went wrong",
                "markdown": f"The request could not be completed ({type(exc).__name__}). The error has been logged with trace ID {_tid()}.",
            }
        response = await self._finalise(run, result, started)
        conv.remember("assistant", response["answer_markdown"][:2000], intent=parsed.intent, status=response["status"])
        return response

    async def _finalise(self, run: Run, result: dict[str, Any], started: float) -> dict[str, Any]:
        sections = result.get("sections", {})
        markdown = result.get("markdown", "")
        llm_meta: dict[str, Any] | None = None
        if result.get("use_llm") and self.llm.name != "offline" and run.status != "error":
            prompt = compose.llm_user_prompt(run.ev, sections)
            res = await self.llm.generate(compose.SYSTEM_PROMPT, prompt)
            ok, unknown = compose.validate_citations(res.text or "", compose.allowed_markers(run.ev, markdown))
            llm_meta = {
                "model": res.model,
                "latency_ms": round(res.latency_ms, 1),
                "accepted": bool(res.text and ok),
                "error": res.error,
                "unknown_citations": unknown,
            }
            run.steps.append(
                {"id": "L-1", "kind": "llm", "status": "ok" if llm_meta["accepted"] else "rejected", "duration_ms": round(res.latency_ms, 1), **llm_meta}
            )
            if res.text and ok:
                markdown = f"### {result.get('heading', 'Answer')}\n\n{res.text}"
            else:
                run.warnings.append("LLM narrative rejected (missing or unknown citations); showing grounded template answer")
        draft: TicketDraft | None = result.get("draft")
        tc = current_trace()
        return {
            "conversation_id": run.conv.conversation_id,
            "trace_id": tc.trace_id if tc else None,
            "request_id": tc.request_id if tc else None,
            "intent": run.parsed.intent,
            "intent_confidence": run.parsed.confidence,
            "entities": run.parsed.entities.as_dict(),
            "status": run.status,
            "answer_markdown": markdown,
            "sections": sections,
            "citations": [c.model_dump() for c in run.citations],
            "tool_evidence": {ref: cid for cid, ref in run.ev.tool_refs.items()},
            "trace": {"steps": run.steps, "total_ms": round((time.perf_counter() - started) * 1000, 1), "llm": llm_meta},
            "ticket_draft": draft.model_dump() if draft else None,
            "similar_tickets": run.ev.similar_tickets,
            "linked_tickets": run.ev.linked_tickets,
            "requires_confirmation": bool(result.get("requires_confirmation")),
            "warnings": list(dict.fromkeys(run.warnings + run.ev.warnings)),
            "suggestions": result.get("suggestions", []),
            "context": run.conv.context_summary(),
        }

    # ================================================================ reusable steps
    async def resolve_asset(self, run: Run) -> tuple[dict[str, Any] | None, str | None]:
        """Entity -> asset metadata. Uses conversation context when the user says 'this'."""
        e = run.parsed.entities
        asset_id = e.asset_id
        search_ref = None
        if not asset_id and (e.asset_ref or (e.asset_type and not run.conv.asset)):
            query = e.asset_ref or e.asset_type or ""
            found, search_ref = await run.tool(
                "search_assets", {"query": query, "site": e.site, "unit": e.unit, "limit": 5}, f"Resolve asset reference '{query}'"
            )
            if not found or not found["results"]:
                return None, None
            results = found["results"]
            top = results[0]
            if e.asset_ref and float(top.get("match_score") or 0) < 0.5:
                # Weak partial match (e.g. only the word "pump"): ask instead of guessing.
                run.ev.warnings.append("No asset closely matches '" + e.asset_ref + "'. Closest: " + ", ".join(x["asset_name"] for x in results[:3]))
                return None, None
            if len(results) > 1 and results[1].get("match_score") == top.get("match_score") and not e.asset_ref:
                run.warnings.append(
                    "Asset reference was ambiguous; using " + top["asset_name"] + ". Other matches: " + ", ".join(x["asset_name"] for x in results[1:4])
                )
            asset_id = top["asset_id"]
        if not asset_id and run.conv.asset:
            asset_id = run.conv.asset["asset_id"]
        if not asset_id:
            return None, None
        meta, ref = await run.tool("get_asset_metadata", {"asset_id": asset_id}, "Fetch asset context", depends_on=run.call_id(search_ref))
        if meta:
            run.conv.asset = meta
            run.ev.asset, run.ev.asset_ref = meta, ref
        return meta, ref

    async def enrich_alarm(self, run: Run, alarm_id: str, alarm_ref: str | None, *, with_correlation: bool = True) -> None:
        """Alarm -> asset context, priority, recommendations, correlation (parallel), then similar & linked tickets and RAG."""
        ev = run.ev
        alarm = ev.alarm or {}
        asset_id = alarm.get("asset_id")
        dep = run.call_id(alarm_ref)
        lookback = ev.lookback_days

        async def meta() -> None:
            if ev.asset and ev.asset.get("asset_id") == asset_id:
                return
            m, ref = await run.tool("get_asset_metadata", {"asset_id": asset_id}, "Enrich alarm with asset context", dep)
            if m:
                ev.asset, ev.asset_ref = m, ref
                run.conv.asset = m

        async def prio() -> None:
            p, ref = await run.tool("score_alarm_priority", {"alarm_id": alarm_id}, "Score alarm priority", dep)
            ev.priority, ev.priority_ref = p, ref

        async def recs() -> None:
            rc, ref = await run.tool("get_operator_recommendations", {"alarm_id": alarm_id}, "Retrieve recommended operator actions", dep)
            ev.recommendations, ev.recommendations_ref = rc, ref

        async def corr() -> None:
            c, ref = await run.tool(
                "correlate_alarms", {"asset_ids": [asset_id], "lookback_days": lookback, "min_support": 2}, "Find correlated alarms and assets", dep
            )
            ev.correlation, ev.correlation_ref = c, ref
            if c:
                run.conv.correlated_assets = c.get("correlated_assets", [])

        await asyncio.gather(meta(), prio(), recs(), *([corr()] if with_correlation else []))
        await asyncio.gather(self.similar_tickets(run), self.linked_tickets(run) if with_correlation else _noop())
        self.retrieve_guidance(run)

    async def similar_tickets(self, run: Run) -> None:
        ev = run.ev
        alarm, asset = ev.alarm or {}, ev.asset or {}
        causes = " ".join((ev.recommendations or {}).get("likely_causes", []))
        query = f"{alarm.get('alarm_name', '')} {asset.get('asset_name', '')} {asset.get('asset_type', '')} {causes}".strip()
        deps = run.call_id(ev.alarm_ref) + run.call_id(ev.recommendations_ref)
        out, _ = await run.tool(
            "search_similar_tickets",
            {"query": query[:500], "asset_id": alarm.get("asset_id"), "asset_type": asset.get("asset_type"), "alarm_name": alarm.get("alarm_name"), "limit": 5},
            "Search similar historical tickets",
            deps,
        )
        if out:
            ev.similar_tickets = out["results"]
            run.conv.similar_tickets = out["results"]

    async def linked_tickets(self, run: Run) -> None:
        ev = run.ev
        base = (ev.asset or ev.alarm or {}).get("asset_id")
        correlated = [a["asset_id"] for a in (ev.correlation or {}).get("correlated_assets", [])]
        connected = (ev.asset or {}).get("connected_assets", []) if not correlated else []
        ids = list(dict.fromkeys([x for x in [base, *correlated, *connected] if x]))[:25]
        if not ids:
            return
        out, ref = await run.tool(
            "list_tickets", {"asset_ids": ids, "statuses": OPEN_STATUSES}, "Open tickets on asset and correlated assets", run.call_id(ev.correlation_ref)
        )
        if out:
            ev.linked_tickets, ev.linked_ref = out["tickets"], ref

    def retrieve_guidance(self, run: Run, extra_terms: str = "") -> None:
        ev = run.ev
        alarm, asset = ev.alarm or {}, ev.asset or {}
        keywords = " ".join((ev.recommendations or {}).get("procedure_keywords", []))
        names = [alarm["alarm_name"]] if alarm.get("alarm_name") else []
        if ev.summary:
            names += [g.get("alarm_name") for g in ev.summary.get("groups", [])[:3] if g.get("alarm_name")]
        names = list(dict.fromkeys(names))
        query = f"{' '.join(names)} {asset.get('asset_name', '')} {asset.get('asset_type', '')} troubleshooting procedure recommended actions {keywords} {extra_terms}"
        filt = RetrievalFilters(
            asset_type=asset.get("asset_type"), alarm_names=names or None, sites=[asset["site"]] if asset.get("site") else None, strict_asset_type=True
        )
        ev.retrieval = run.rag(query, filt, "Retrieve troubleshooting procedure and knowledge articles")
        esc = run.rag(
            "incident priority definitions escalation response notify", RetrievalFilters(doc_types=["escalation_procedure"]), "Retrieve escalation rules", k=2
        )
        if esc and esc.hits:
            ev.escalation_citation = next((c for c in run.citations if c.chunk_id == esc.hits[0].chunk.chunk_id), None)

    def make_draft(self, run: Run) -> TicketDraft:
        priority, notes = compose.reconcile_priority(run.ev)
        run.ev.conflicts += notes
        fields = TicketFields(**compose.draft_fields(run.ev, priority))
        draft = TicketDraft(
            draft_id=self.store.new_draft_id(),
            conversation_id=run.conv.conversation_id,
            fields=fields,
            rationale={"priority_conflicts": notes, "trace_id": _tid(), "citations": [c.ref + " " + c.doc_id for c in run.citations]},
        )
        draft.history.append({"ts": time.time(), "action": "created", "by": "copilot"})
        self.store.save_draft(draft)
        run.conv.draft_id = draft.draft_id
        self.audit.record(
            "draft_created",
            actor="copilot",
            conversation_id=run.conv.conversation_id,
            trace_id=_tid(),
            draft_id=draft.draft_id,
            priority=priority,
            asset_id=fields.asset_id,
        )
        return draft

    def _remember_alarm(self, run: Run) -> None:
        if run.ev.alarm:
            run.conv.alarm = run.ev.alarm
            run.conv.enrichment = {
                "priority": run.ev.priority,
                "recommendations": run.ev.recommendations,
                "correlation": run.ev.correlation,
            }
        run.conv.citations = [c.model_dump() for c in run.citations]

    # ================================================================ workflows
    async def _wf_prepare_incident(self, run: Run) -> dict[str, Any]:
        e, conv, ev = run.parsed.entities, run.conv, run.ev
        scope = {"site": e.site, "unit": e.unit}
        use_context = not (e.site or e.unit or e.asset_ref or e.asset_id) and conv.alarm is not None
        if use_context:
            run.plan(
                [
                    "get_alarm (from context)",
                    "get_asset_metadata | score_alarm_priority | get_operator_recommendations | correlate_alarms",
                    "search_similar_tickets | list_tickets",
                    "RAG: procedures + escalation",
                    "compose draft",
                ]
            )
            alarm_id = conv.alarm["alarm_id"]  # type: ignore[index]
            alarm_out, alarm_ref = await run.tool("get_alarm", {"alarm_id": alarm_id}, "Refresh alarm from conversation context")
        else:
            run.plan(
                [
                    "resolve scope (site/unit/asset)",
                    "list_alarms status=active (all pages)",
                    "rank_alarms_by_priority",
                    "get_alarm",
                    "get_asset_metadata | score_alarm_priority | get_operator_recommendations | correlate_alarms",
                    "search_similar_tickets | list_tickets",
                    "RAG: procedures + escalation",
                    "compose draft",
                ]
            )
            asset_dep: list[str] = []
            if e.asset_ref or e.asset_id:
                asset, aref = await self.resolve_asset(run)
                if not asset:
                    return self._clarify(run, f"I could not resolve the asset '{e.asset_ref or e.asset_id}'. Which asset do you mean?")
                scope = {"asset_id": asset["asset_id"]}
                asset_dep = run.call_id(aref)
            if not any(scope.values()):
                return self._clarify(
                    run, "Which site, unit or asset should I look at? For example: *Prepare an incident for the highest-priority active alarm in EastRefinery.*"
                )
            listed, list_ref = await run.tool(
                "list_alarms",
                {
                    **scope,
                    "status": e.status or ["active", "acknowledged"],
                    "severity": e.severity,
                    "sort_by": "priority",
                    "sort_order": "desc",
                    "page_size": 100,
                    "fetch_all_pages": True,
                    "max_pages": 5,
                },
                "Retrieve active alarms in scope",
                asset_dep,
            )
            if listed is None:
                run.status = "error"
                return {
                    "heading": "Alarm data unavailable",
                    "markdown": "I could not retrieve alarms from the Alarm Management system, so no incident was prepared.\n\n"
                    + "\n".join(f"- {w}" for w in run.warnings),
                }
            alarms = listed["alarms"]
            if not alarms:
                where = scope.get("site") or scope.get("unit") or (ev.asset or {}).get("asset_name")
                return {"heading": "No active alarms", "markdown": f"There are no active alarms in **{where}**{compose.r(list_ref)}. No incident is needed."}
            candidates = [a["alarm_id"] for a in alarms[:25]]
            ranked, rank_ref = await run.tool(
                "rank_alarms_by_priority", {"alarm_ids": candidates}, "Rank candidate alarms by priority score", run.call_id(list_ref)
            )
            if ranked and ranked.get("top_alarm_id"):
                alarm_id = ranked["top_alarm_id"]
                if ranked.get("failures"):
                    run.warnings.append(f"Priority scoring failed for {ranked['failures']} alarm(s); ranking may be incomplete")
            else:
                alarm_id = alarms[0]["alarm_id"]
                run.warnings.append("Priority ranking unavailable; fell back to source-system priority ordering")
            ev.candidates_considered = len(alarms)
            ev.rank_ref = " ".join(x for x in (list_ref, rank_ref) if x)
            alarm_out, alarm_ref = await run.tool("get_alarm", {"alarm_id": alarm_id}, "Fetch selected alarm detail", run.call_id(rank_ref or list_ref))
        if not alarm_out:
            run.status = "error"
            return {
                "heading": "Alarm detail unavailable",
                "markdown": "The selected alarm could not be retrieved.\n\n" + "\n".join(f"- {w}" for w in run.warnings),
            }
        ev.alarm, ev.alarm_ref = alarm_out["alarm"], alarm_ref
        await self.enrich_alarm(run, ev.alarm["alarm_id"], alarm_ref)
        draft = self.make_draft(run)
        self._remember_alarm(run)
        sections = compose.build_sections(ev)
        heading = f"Incident draft: {ev.alarm['alarm_name']} on {ev.alarm['asset_name']}"
        md = compose.render_markdown(ev, sections, heading)
        md += f"\n\n**Draft {draft.draft_id}** is ready in the ticket panel with priority **{draft.fields.priority}**. Review or edit the fields, then approve to create the ticket. Nothing has been written yet."
        return {
            "heading": heading,
            "markdown": md,
            "sections": sections,
            "draft": draft,
            "use_llm": True,
            "suggestions": [
                "Find similar historical tickets for this alarm",
                "Add the applicable troubleshooting procedure to the ticket draft",
                "Show open tickets linked to correlated assets",
            ],
        }

    async def _wf_investigate_asset(self, run: Run) -> dict[str, Any]:
        e, ev = run.parsed.entities, run.ev
        run.plan(
            [
                "search_assets -> get_asset_metadata",
                "get_alarm_summary | get_alarm_trends | correlate_alarms",
                "list_alarms (latest of top recurring alarm)",
                "get_operator_recommendations | score_alarm_priority",
                "search_similar_tickets | list_tickets",
                "RAG: operating procedure + escalation",
                "compose answer",
            ]
        )
        asset, aref = await self.resolve_asset(run)
        if not asset:
            if run.status == "partial" and any("unavailable" in w or "unreachable" in w for w in run.warnings):
                run.status = "error"
                return {
                    "heading": "Alarm system unavailable",
                    "markdown": "I could not reach the Alarm Management MCP server to resolve the asset.\n\n" + "\n".join(f"- {w}" for w in run.warnings),
                }
            return self._clarify(run, "Which asset should I investigate? Please give its name or tag (for example *Boiler Feed Pump 101* or *K-501*).")
        dep = run.call_id(aref)
        lookback = ev.lookback_days
        aid = asset["asset_id"]
        sev = e.severity

        async def summary() -> None:
            s, ref = await run.tool(
                "get_alarm_summary",
                {
                    "asset_ids": [aid],
                    "lookback_days": lookback,
                    "severity": sev,
                    "group_by": ["alarm_name"],
                    "kpis": ["alarm_count", "recurring_rate", "avg_ack_delay", "critical_count"],
                },
                "Summarise alarms and recurrence",
                dep,
            )
            ev.summary, ev.summary_ref = s, ref

        async def trends() -> None:
            t, ref = await run.tool(
                "get_alarm_trends",
                {"asset_ids": [aid], "lookback_days": lookback, "bucket": "weekly", "metrics": ["alarm_count"], "severity": sev},
                "Weekly alarm trend",
                dep,
            )
            ev.trends, ev.trends_ref = t, ref

        async def corr() -> None:
            c, ref = await run.tool(
                "correlate_alarms",
                {"asset_ids": [aid], "lookback_days": lookback, "severity_threshold": "high" if sev else "medium", "min_support": 2},
                "Correlate alarms (contributing factors)",
                dep,
            )
            ev.correlation, ev.correlation_ref = c, ref
            if c:
                run.conv.correlated_assets = c.get("correlated_assets", [])

        await asyncio.gather(summary(), trends(), corr())
        groups = (ev.summary or {}).get("groups", [])
        if ev.summary is not None and not groups:
            return {
                "heading": f"No matching alarms on {asset['asset_name']}",
                "markdown": f"No {'/'.join(sev) if sev else ''} alarms were recorded on **{asset['asset_name']}** in the last {lookback} days{compose.r(ev.summary_ref)}.",
                "sections": compose.build_sections(ev),
            }
        if groups:
            top_name = groups[0]["alarm_name"]
            latest, lref = await run.tool(
                "list_alarms",
                {"asset_id": aid, "alarm_name": top_name, "page_size": 1, "sort_by": "start_time", "sort_order": "desc"},
                f"Latest '{top_name}' activation",
                run.call_id(ev.summary_ref),
            )
            if latest and latest["alarms"]:
                ev.alarm, ev.alarm_ref = latest["alarms"][0], lref
                await self.enrich_alarm(run, ev.alarm["alarm_id"], lref, with_correlation=False)
                await self.linked_tickets(run)
            else:
                self.retrieve_guidance(run)
        else:
            self.retrieve_guidance(run)
        self._remember_alarm(run)
        sevtxt = "high-severity " if sev else ""
        heading = f"Investigation: recurring {sevtxt}alarms on {asset['asset_name']} (last {lookback} days)"
        sections = compose.build_sections(ev)
        _, notes = compose.reconcile_priority(ev)
        ev.conflicts += notes
        md = compose.render_markdown(ev, sections, heading)
        return {
            "heading": heading,
            "markdown": md,
            "sections": sections,
            "use_llm": True,
            "suggestions": [
                "Prepare an incident for this alarm",
                "Find similar historical tickets for this alarm",
                "Show open tickets linked to correlated assets",
            ],
        }

    async def _wf_similar_tickets(self, run: Run) -> dict[str, Any]:
        e, conv, ev = run.parsed.entities, run.conv, run.ev
        run.plan(["use alarm from context or resolve asset", "search_similar_tickets", "RAG: resolution notes"])
        ctx_alarm = conv.alarm
        if (
            ctx_alarm
            and e.asset_type
            and ctx_alarm.get("asset_type", (conv.asset or {}).get("asset_type")) not in (None, e.asset_type)
            and not e.refers_to_context
        ):
            ctx_alarm = None
        if (e.asset_ref or e.asset_id) or not ctx_alarm:
            asset, aref = await self.resolve_asset(run)
            if not asset:
                return self._clarify(
                    run, "Which alarm or asset should I compare against? Prepare an incident or investigate an asset first, or name the asset."
                )
            latest, lref = await run.tool(
                "list_alarms",
                {"asset_id": asset["asset_id"], "severity": ["high", "critical"], "page_size": 1, "sort_by": "start_time", "sort_order": "desc"},
                "Latest high-severity alarm on asset",
                run.call_id(aref),
            )
            if latest and latest["alarms"]:
                ev.alarm, ev.alarm_ref = latest["alarms"][0], lref
        else:
            ev.alarm, ev.asset = ctx_alarm, conv.asset
            ev.recommendations = conv.enrichment.get("recommendations")
        if not ev.alarm:
            return self._clarify(run, "I could not find a recent high-severity alarm to compare against.")
        await self.similar_tickets(run)
        asset_type = (ev.asset or {}).get("asset_type")
        ev.retrieval = run.rag(
            f"{ev.alarm['alarm_name']} {(ev.asset or {}).get('asset_name', '')} root cause resolution lesson",
            RetrievalFilters(doc_types=["resolution_notes", "knowledge_article"], asset_type=asset_type, alarm_names=[ev.alarm["alarm_name"]]),
            "Retrieve historical resolution notes",
        )
        self._remember_alarm(run)
        sections = {k: v for k, v in compose.build_sections(ev).items() if k in ("similar_tickets", "likely_causes", "affected_asset")}
        heading = f"Similar historical tickets for {ev.alarm['alarm_name']} on {ev.alarm['asset_name']}"
        if not ev.similar_tickets:
            md = f"### {heading}\n\nNo sufficiently similar historical tickets were found."
        else:
            md = compose.render_markdown(
                compose.Evidence(intent="", question="", similar_tickets=ev.similar_tickets), {"similar_tickets": sections["similar_tickets"]}, heading
            )
            if sections.get("likely_causes"):
                md += "\n\n**Root causes seen before:**\n\n" + "\n".join(f"- {c['cause']} {' '.join(c['sources'])}" for c in sections["likely_causes"])
        if conv.draft_id:
            md += "\n\nThese tickets are already linked in the current draft." if ev.similar_tickets else ""
        return {
            "heading": heading,
            "markdown": md,
            "sections": sections,
            "use_llm": False,
            "suggestions": ["Summarize the issue, likely cause, affected asset, and recommended action"],
        }

    async def _wf_summarize_issue(self, run: Run) -> dict[str, Any]:
        conv, ev = run.conv, run.ev
        run.plan(
            [
                "get_alarm (refresh from context)",
                "get_asset_metadata",
                "get_operator_recommendations",
                "reuse similar tickets + citations",
                "RAG: procedure",
                "compose summary",
            ]
        )
        if not conv.alarm:
            return self._clarify(run, "There is no alarm in this conversation yet. Ask me to prepare an incident or investigate an asset first.")
        a, aref = await run.tool("get_alarm", {"alarm_id": conv.alarm["alarm_id"]}, "Refresh alarm status")
        ev.alarm, ev.alarm_ref = (a["alarm"] if a else conv.alarm), aref
        m, mref = await run.tool("get_asset_metadata", {"asset_id": ev.alarm["asset_id"]}, "Refresh asset context", run.call_id(aref))
        ev.asset, ev.asset_ref = (m or conv.asset), mref
        rc, rref = await run.tool("get_operator_recommendations", {"alarm_id": ev.alarm["alarm_id"]}, "Recommended actions", run.call_id(aref))
        ev.recommendations, ev.recommendations_ref = rc, rref
        ev.priority = conv.enrichment.get("priority")
        ev.similar_tickets = conv.similar_tickets
        self.retrieve_guidance(run)
        sections = compose.build_sections(ev)
        keep = ("affected_asset", "triggering_alarm", "likely_causes", "recommended_actions", "procedure")
        sections = {k: v for k, v in sections.items() if k in keep}
        heading = f"Issue summary: {ev.alarm['alarm_name']} on {ev.alarm['asset_name']}"
        md = compose.render_markdown(ev, sections, heading)
        return {
            "heading": heading,
            "markdown": md,
            "sections": sections,
            "use_llm": True,
            "suggestions": ["Add the applicable troubleshooting procedure to the ticket draft"],
        }

    async def _wf_add_procedure(self, run: Run) -> dict[str, Any]:
        conv, ev = run.conv, run.ev
        run.plan(["load current draft", "RAG: operating procedure / troubleshooting guide (filtered)", "update draft (no write to ticketing)"])
        draft = self.store.drafts.get(conv.draft_id or "")
        if not draft:
            return self._clarify(run, "There is no ticket draft yet. Ask me to prepare an incident first.")
        f = draft.fields
        ev.alarm, ev.asset = conv.alarm, conv.asset
        asset_type = (conv.asset or {}).get("asset_type")
        res = run.rag(
            f"{f.alarm_name} {f.asset_name} troubleshooting procedure steps",
            RetrievalFilters(
                doc_types=["operating_procedure", "troubleshooting_guide"],
                asset_type=asset_type,
                alarm_names=[f.alarm_name] if f.alarm_name else None,
                strict_asset_type=True,
            ),
            "Retrieve applicable troubleshooting procedure",
        )
        ev.retrieval = res
        if not res or res.confidence in ("low", "none"):
            return {
                "heading": "No applicable procedure found",
                "markdown": "I could not find a procedure that confidently matches this alarm, so the draft was not changed. Please attach the procedure manually.",
                "draft": draft,
            }
        # Prefer the best procedure section that is not already in the draft.
        chosen = None
        for hit in res.hits:
            steps = compose.chunk_steps(hit.chunk.text, 8)
            if not steps:
                continue
            already = hit.chunk.doc_id in f.description and hit.chunk.section in f.description
            if chosen is None or (chosen[2] and not already):
                chosen = (hit.chunk, steps, already)
            if not already:
                break
        if chosen is None:
            return {
                "heading": "No applicable procedure found",
                "markdown": "The retrieved documents contain no step-by-step procedure for this alarm; the draft was not changed.",
                "draft": draft,
            }
        top, steps, already = chosen
        cit = next(c for c in run.citations if c.chunk_id == top.chunk_id)
        block = f"\nApplicable troubleshooting procedure ({top.doc_id} v{top.version}, {top.section}):\n" + "\n".join(
            f"  {i}. {st}" for i, st in enumerate(steps, start=1)
        )
        if already:
            md_note = "This procedure section was already in the draft; no change made."
        else:
            f.description = (f.description + "\n" + block)[:20000]
            md_note = "The procedure has been appended to the draft description."
        if f.alarm_name and f.alarm_name.lower() not in f"{top.section} {top.text}".lower():
            md_note += f" Note: no indexed procedure section covers '{f.alarm_name}' specifically; this is the closest applicable guidance for the asset type - please verify."
        if top.doc_id not in {d.doc_id for d in f.linked_documents}:
            from copilot.state import DocumentLink

            f.linked_documents.append(DocumentLink(doc_id=top.doc_id, title=top.title[:200], section=top.section[:200]))
        draft.history.append({"ts": time.time(), "action": "procedure_added", "by": "copilot", "doc_id": top.doc_id})
        self.store.save_draft(draft)
        self.audit.record(
            "draft_updated",
            actor="copilot",
            conversation_id=conv.conversation_id,
            trace_id=_tid(),
            draft_id=draft.draft_id,
            change="procedure_added",
            doc_id=top.doc_id,
        )
        heading = f"Added {top.doc_id} to draft {draft.draft_id}"
        md = (
            f"### {heading}\n\n**{top.title} - {top.section}** {cit.ref}\n\n"
            + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, start=1))
            + f"\n\n{md_note} Nothing has been submitted."
        )
        return {
            "heading": heading,
            "markdown": md,
            "sections": {"procedure": {"doc_id": top.doc_id, "section": top.section, "ref": cit.ref, "steps": steps}},
            "draft": draft,
        }

    async def _wf_linked_tickets(self, run: Run) -> dict[str, Any]:
        e, conv, ev = run.parsed.entities, run.conv, run.ev
        run.plan(["resolve asset (context or entity)", "correlate_alarms", "list_tickets (asset + correlated assets, open)"])
        if e.asset_ref or e.asset_id or not conv.asset:
            asset, _ = await self.resolve_asset(run)
            if not asset:
                return self._clarify(run, "Which asset's correlated tickets do you want? Name the asset or prepare an incident first.")
        else:
            ev.asset = conv.asset
        aid = ev.asset["asset_id"]  # type: ignore[index]
        c, cref = await run.tool("correlate_alarms", {"asset_ids": [aid], "lookback_days": ev.lookback_days, "min_support": 2}, "Find correlated assets", [])
        ev.correlation, ev.correlation_ref = c, cref
        if c:
            conv.correlated_assets = c.get("correlated_assets", [])
        elif conv.correlated_assets:
            ev.correlation = {"correlations": [], "correlated_assets": conv.correlated_assets}
            run.warnings.append("Using correlated assets remembered from earlier in this conversation")
        await self.linked_tickets(run)
        names = [a.get("asset_name") for a in (ev.correlation or {}).get("correlated_assets", [])]
        heading = f"Open tickets on {ev.asset['asset_name']} and correlated assets"  # type: ignore[index]
        sections = {k: v for k, v in compose.build_sections(ev).items() if k in ("correlations", "linked_open_tickets")}
        md = f"### {heading}\n\nCorrelated assets{compose.r(cref)}: {', '.join(n for n in names if n) or 'none found (falling back to connected assets)'}.\n\n"
        if ev.linked_tickets:
            md += "\n".join(
                f"- [{t['ticket_id']}] {t['title']} - {t.get('asset_name')} ({t['status']}, {t['priority']}){compose.r(ev.linked_ref)}"
                for t in ev.linked_tickets
            )
        else:
            md += "No open tickets were found on these assets."
        return {"heading": heading, "markdown": md, "sections": sections}

    async def _wf_create_ticket(self, run: Run) -> dict[str, Any]:
        run.plan(["locate draft", "require explicit human approval (GUI)"])
        draft = self.store.drafts.get(run.conv.draft_id or "")
        if not draft:
            return self._clarify(run, "There is no draft to submit. Ask me to prepare an incident first.")
        if draft.status == "submitted":
            return {"heading": "Already submitted", "markdown": f"Draft {draft.draft_id} was already submitted as **{draft.ticket_id}**.", "draft": draft}
        return {
            "heading": "Confirmation required",
            "markdown": f"Ticket creation is a write operation and needs your explicit approval. Review draft **{draft.draft_id}** in the ticket panel and press **Approve & create ticket**. I have not created anything.",
            "draft": draft,
            "requires_confirmation": True,
        }

    async def _wf_knowledge_question(self, run: Run) -> dict[str, Any]:
        e, ev, conv = run.parsed.entities, run.ev, run.conv
        run.plan(["RAG: hybrid search over document corpus", "compose cited answer"])
        asset_type = e.asset_type or (conv.asset or {}).get("asset_type")
        res = run.rag(run.parsed.text, RetrievalFilters(asset_type=asset_type, sites=[e.site] if e.site else None), "Answer from documentation")
        ev.retrieval = res
        if not res or res.confidence == "none" or not res.hits:
            return {"heading": "No documentation found", "markdown": "I could not find documentation covering that question in the indexed corpus."}
        if res.confidence == "low":
            return {
                "heading": "Insufficient documentation",
                "markdown": "I found only weakly related documentation, so I won't guess. Closest matches:\n\n"
                + "\n".join(f"- {c.ref} {c.title} - {c.section}" for c in run.citations[:3]),
            }
        lines = [f"### Answer from documentation ({res.confidence} confidence)"]
        for c in run.citations[:3]:
            lines.append(f"**{c.title} - {c.section}** {c.ref}\n\n{c.snippet}")
        return {"heading": "Answer from documentation", "markdown": "\n\n".join(lines), "use_llm": True}

    async def _wf_list_tools(self, run: Run) -> dict[str, Any]:
        run.plan(["MCP tool discovery"])
        await self.registry.discover()
        lines = ["### Available MCP tools"]
        for server, st in self.registry.server_status.items():
            lines.append(f"**{server}** - {st['status']} ({st.get('tool_count', 0)} tools)")
            lines += [
                f"- `{t.name}` {'(read-only)' if t.read_only else '(WRITE, approval required)'}: {t.description.splitlines()[0] if t.description else ''}"
                for t in self.registry.tools.values()
                if t.server == server
            ]
        return {"heading": "Available MCP tools", "markdown": "\n\n".join(lines[:3]) + "\n" + "\n".join(lines[3:])}

    async def _wf_help(self, run: Run) -> dict[str, Any]:
        run.plan([])
        return {
            "heading": "Incident & Ticket Enrichment Copilot",
            "markdown": "I prepare enriched incident tickets from live alarm data (via MCP) and site documentation (via RAG). Try:\n\n"
            "- *Prepare an incident for the highest-priority active alarm in EastRefinery.*\n"
            "- *Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days.*\n"
            "- *Find similar historical tickets for this compressor alarm.*\n"
            "- *Show open tickets linked to correlated assets.*",
        }

    def _clarify(self, run: Run, question: str) -> dict[str, Any]:
        run.status = "needs_clarification" if run.status == "ok" else run.status
        return {"heading": "More information needed", "markdown": question}

    # ================================================================ approval (write path)
    async def approve_draft(self, draft_id: str, fields: TicketFields, approved_by: str, mint: Any) -> dict[str, Any]:
        draft = self.store.drafts.get(draft_id)
        if not draft:
            return {"ok": False, "error": {"code": "not_found", "message": "Draft not found"}}
        if draft.status == "submitted":
            return {"ok": True, "ticket_id": draft.ticket_id, "draft": draft.model_dump(), "idempotent_replay": True}
        draft.fields = fields
        payload = fields.model_dump(mode="json")
        token = mint(payload)
        self.audit.record(
            "approval_granted",
            actor=approved_by,
            conversation_id=draft.conversation_id,
            trace_id=_tid(),
            draft_id=draft_id,
            priority=fields.priority,
            asset_id=fields.asset_id,
        )
        if not self.registry.tools:
            await self.registry.discover()
        rec = await self.registry.call(
            "create_ticket",
            {"draft": payload, "approval_token": token, "approved_by": approved_by, "idempotency_key": draft_id},
            purpose="Create approved ticket (write)",
        )
        trace = rec.as_trace()
        if not rec.ok:
            draft.status = "failed"
            draft.history.append({"ts": time.time(), "action": "create_failed", "by": approved_by, "error": rec.error})
            self.store.save_draft(draft)
            self.audit.record(
                "ticket_create_failed", actor=approved_by, conversation_id=draft.conversation_id, trace_id=_tid(), draft_id=draft_id, error=rec.error
            )
            return {"ok": False, "error": rec.error, "trace": trace, "draft": draft.model_dump()}
        ticket = (rec.output or {}).get("ticket", {})
        draft.status, draft.ticket_id = "submitted", ticket.get("ticket_id")
        draft.history.append({"ts": time.time(), "action": "submitted", "by": approved_by, "ticket_id": draft.ticket_id})
        self.store.save_draft(draft)
        self.audit.record(
            "ticket_created", actor=approved_by, conversation_id=draft.conversation_id, trace_id=_tid(), draft_id=draft_id, ticket_id=draft.ticket_id
        )
        log_event(log, "ticket.created", ticket_id=draft.ticket_id, draft_id=draft_id)
        return {
            "ok": True,
            "ticket_id": draft.ticket_id,
            "ticket": ticket,
            "trace": trace,
            "draft": draft.model_dump(),
            "idempotent_replay": (rec.output or {}).get("idempotent_replay", False),
        }


async def _noop() -> None:
    return None
