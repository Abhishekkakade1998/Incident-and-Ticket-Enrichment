"""Copilot backend HTTP API (FastAPI)."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from common import approval
from common.logging import configure_logging, log_event
from common.tracing import TraceContext, reset_trace, set_trace
from copilot.audit import AuditLog
from copilot.config import Settings, get_settings
from copilot.llm import build_provider
from copilot.mcp_client import HttpMcpConnection, ToolRegistry
from copilot.orchestrator import Orchestrator
from copilot.state import ConversationStore, TicketFields
from rag.retrieval.retriever import RetrievalFilters, Retriever

log = logging.getLogger("copilot.api")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")


class DraftUpdate(BaseModel):
    fields: TicketFields


class ApproveRequest(BaseModel):
    approved_by: str = Field(min_length=1, max_length=120)
    confirm: bool = Field(description="Must be true: explicit confirmation of the write operation")
    fields: TicketFields


class RagSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    doc_types: list[str] | None = None
    asset_type: str | None = None
    alarm_names: list[str] | None = None
    k: int = Field(default=5, ge=1, le=20)


def _load_retriever(settings: Settings) -> Retriever | None:
    path = Path(settings.rag_index_path)
    if not path.exists() and settings.rag_auto_ingest:
        from rag.ingestion.ingest import ingest

        ingest(Path(settings.document_path), path)
    if not path.exists():
        log_event(log, "rag.index.missing", level=logging.WARNING, path=str(path))
        return None
    return Retriever.from_path(path)


def create_app(
    settings: Settings | None = None,
    *,
    registry: ToolRegistry | None = None,
    retriever: Retriever | None = None,
    audit: AuditLog | None = None,
) -> FastAPI:
    s = settings or get_settings()
    reg = registry or ToolRegistry(
        [
            HttpMcpConnection("alarm-management", s.alarm_mcp_url, s.alarm_mcp_token),
            HttpMcpConnection("ticketing", s.ticketing_mcp_url, s.ticketing_mcp_token),
        ],
        default_timeout_s=s.mcp_tool_timeout_s,
    )
    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rtr = retriever if retriever is not None else _load_retriever(s)
        aud = audit or AuditLog(s.audit_db_path)
        llm = build_provider(s.llm_provider, s.llm_api_key, s.llm_model, s.llm_timeout_s)
        state["orch"] = Orchestrator(reg, rtr, llm, ConversationStore(), aud, default_lookback_days=s.default_lookback_days, top_k=s.rag_top_k)
        state["retriever"] = rtr
        try:
            await reg.discover()
        except Exception:
            log.exception("initial MCP discovery failed")
        yield

    app = FastAPI(title="Incident & Ticket Enrichment Copilot", version="1.0.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["GET", "POST", "PUT"], allow_headers=["*"])

    def orch() -> Orchestrator:
        return state["orch"]  # type: ignore[no-any-return]

    @app.middleware("http")
    async def trace_mw(request: Request, call_next: Any) -> Any:
        ctx = TraceContext.from_mapping(dict(request.headers), client_id="copilot-backend")
        token = set_trace(ctx)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            reset_trace(token)
        response.headers["trace_id"] = ctx.trace_id
        response.headers["x-request-id"] = ctx.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:"
        if request.url.path.startswith("/api"):
            log_event(
                log,
                "http.request",
                path=request.url.path,
                method=request.method,
                status=response.status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                trace_id=ctx.trace_id,
            )
        return response

    # ---------------------------------------------------------------- health
    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> JSONResponse:
        if not reg.tools:
            await reg.discover()
        servers = reg.server_status
        ok = bool(servers) and all(v.get("status") == "ok" for v in servers.values())
        body = {
            "status": "ready" if ok else "degraded",
            "mcp_servers": servers,
            "rag_index": state.get("retriever") is not None,
            "llm_provider": s.llm_provider,
        }
        return JSONResponse(body, status_code=200 if ok else 503)

    # ---------------------------------------------------------------- MCP discovery view
    @app.get("/api/tools")
    async def tools(refresh: bool = False) -> dict[str, Any]:
        if refresh or not reg.tools:
            await reg.discover()
        return {"servers": reg.server_status, "discovered_at": reg.discovered_at, "tools": [t.as_dict() for t in reg.tools.values()]}

    # ---------------------------------------------------------------- chat
    @app.post("/api/chat")
    async def chat(req: ChatRequest, request: Request) -> dict[str, Any]:
        user = request.headers.get("x-user", "operator")[:120]
        return await orch().handle(req.message.strip(), req.conversation_id, user=user)

    @app.get("/api/conversations/{conversation_id}")
    async def conversation(conversation_id: str) -> dict[str, Any]:
        conv = orch().store.get(conversation_id)
        if not conv:
            raise HTTPException(404, "conversation not found")
        return {"conversation_id": conv.conversation_id, "messages": conv.messages, "context": conv.context_summary()}

    # ---------------------------------------------------------------- drafts + approval
    @app.get("/api/drafts/{draft_id}")
    async def get_draft(draft_id: str) -> dict[str, Any]:
        d = orch().store.drafts.get(draft_id)
        if not d:
            raise HTTPException(404, "draft not found")
        return d.model_dump()

    @app.put("/api/drafts/{draft_id}")
    async def update_draft(draft_id: str, body: DraftUpdate, request: Request) -> dict[str, Any]:
        o = orch()
        d = o.store.drafts.get(draft_id)
        if not d:
            raise HTTPException(404, "draft not found")
        if d.status == "submitted":
            raise HTTPException(409, "draft already submitted")
        d.fields = body.fields
        d.history.append({"ts": time.time(), "action": "edited", "by": request.headers.get("x-user", "operator")})
        o.store.save_draft(d)
        o.audit.record(
            "draft_updated", actor=request.headers.get("x-user", "operator"), conversation_id=d.conversation_id, draft_id=draft_id, change="user_edit"
        )
        return d.model_dump()

    @app.post("/api/drafts/{draft_id}/approve")
    async def approve(draft_id: str, body: ApproveRequest) -> JSONResponse:
        if body.confirm is not True:
            raise HTTPException(400, "explicit confirmation (confirm=true) is required to create a ticket")
        mint = partial(approval.mint, key=s.approval_signing_key, ttl_s=s.approval_ttl_s)
        result = await orch().approve_draft(draft_id, body.fields, body.approved_by, mint)
        if not result.get("ok") and (result.get("error") or {}).get("code") == "not_found":
            raise HTTPException(404, "draft not found")
        return JSONResponse(result, status_code=200 if result.get("ok") else 502)

    @app.post("/api/drafts/{draft_id}/reject")
    async def reject(draft_id: str, request: Request) -> dict[str, Any]:
        o = orch()
        d = o.store.drafts.get(draft_id)
        if not d:
            raise HTTPException(404, "draft not found")
        d.status = "rejected"
        d.history.append({"ts": time.time(), "action": "rejected", "by": request.headers.get("x-user", "operator")})
        o.audit.record("draft_rejected", actor=request.headers.get("x-user", "operator"), conversation_id=d.conversation_id, draft_id=draft_id)
        return d.model_dump()

    # ---------------------------------------------------------------- audit + rag inspection
    @app.get("/api/audit")
    async def audit_events(conversation_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        return {"events": orch().audit.list(conversation_id, limit)}

    @app.post("/api/rag/search")
    async def rag_search(req: RagSearchRequest) -> dict[str, Any]:
        rtr = state.get("retriever")
        if rtr is None:
            raise HTTPException(503, "RAG index not loaded")
        res = rtr.search(req.query, RetrievalFilters(doc_types=req.doc_types, asset_type=req.asset_type, alarm_names=req.alarm_names), k=req.k)
        return {
            "confidence": res.confidence,
            "warnings": res.warnings,
            "citations": [c.model_dump() for c in res.citations()],
            "excluded": res.excluded_chunk_ids,
        }

    @app.exception_handler(ValidationError)
    async def _validation(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse({"detail": exc.errors(include_url=False)}, status_code=422)

    # ---------------------------------------------------------------- GUI (optional static hosting)
    fdir = Path(s.frontend_dir)
    if fdir.exists():
        app.mount("/static", StaticFiles(directory=fdir), name="static")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(fdir / "index.html")

    return app


def run() -> None:
    import uvicorn

    s = get_settings()
    configure_logging("copilot-backend", s.log_level)
    import os

    uvicorn.run(create_app(s), host=os.environ.get("COPILOT_HOST", "127.0.0.1"), port=int(os.environ.get("COPILOT_PORT", "8080")), log_config=None)


if __name__ == "__main__":
    run()
