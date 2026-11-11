"""Ticketing MCP server (secondary source system).

Read tools: similar-ticket search, list/get tickets.
Write tools: ``create_ticket`` and ``add_ticket_comment`` are gated by a human-approval
token (see ``common.approval``) and use idempotency keys, so a retried call never
creates duplicates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.requests import Request
from starlette.responses import JSONResponse

from common import approval
from common.mcp_support import CallRecorder, SourceCall, run_tool, tool_error
from common.tracing import current_trace
from connectors.http import RetryPolicy
from connectors.ticketing_api import TicketingClient

SERVER_NAME = "ticketing"
TicketStatus = Literal["open", "in_progress", "resolved", "closed"]
READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)


class TicketingMcpSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ticketing_api_url: str = "http://localhost:8100"
    ticketing_api_token: str = "ticket-demo-token"
    ticketing_mcp_token: str | None = None
    ticketing_mcp_host: str = "127.0.0.1"
    ticketing_mcp_port: int = 9002
    approval_signing_key: str = "change-me-approval-key"
    source_timeout_s: float = 8.0
    source_max_retries: int = 2
    tool_timeout_s: float = 20.0
    log_level: str = "INFO"


class Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class Ticket(Open):
    ticket_id: str
    title: str
    status: TicketStatus
    priority: str
    asset_id: str
    asset_name: str | None = None
    alarm_name: str | None = None
    created_at: str
    resolution_notes: str | None = None


class SimilarTicket(Ticket):
    similarity: float
    match_reasons: list[str]


class Out(Open):
    trace_id: str | None = None
    source_calls: list[SourceCall] = Field(default_factory=list)


class SimilarOutput(Out):
    results: list[SimilarTicket]
    total_candidates: int


class TicketListOutput(Out):
    tickets: list[Ticket]
    total: int
    pages_fetched: int


class TicketOutput(Out):
    ticket: Ticket
    idempotent_replay: bool = False


class CommentOutput(Out):
    ticket_id: str
    comment: dict[str, Any]


class DocumentLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str = Field(max_length=80)
    title: str = Field(max_length=200)
    section: str | None = Field(default=None, max_length=200)


class TicketDraft(BaseModel):
    """Exact payload that the user approved in the GUI."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=5, max_length=200)
    description: str = Field(min_length=10, max_length=20000)
    priority: Literal["P1", "P2", "P3", "P4"]
    asset_id: str = Field(pattern=r"^AST-[A-Z]{3}-\d{3}$")
    asset_name: str | None = None
    site: str | None = None
    alarm_ids: list[str] = Field(default_factory=list, max_length=50)
    alarm_name: str | None = None
    category: str = "alarm_incident"
    assignee_group: str | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)
    linked_documents: list[DocumentLink] = Field(default_factory=list, max_length=20)
    similar_ticket_ids: list[str] = Field(default_factory=list, max_length=20)


def _tid() -> str | None:
    t = current_trace()
    return t.trace_id if t else None


def build_server(settings: TicketingMcpSettings | None = None, client: TicketingClient | None = None) -> FastMCP:
    s = settings or TicketingMcpSettings()
    api = client or TicketingClient(
        s.ticketing_api_url, s.ticketing_api_token, timeout_s=s.source_timeout_s, retry=RetryPolicy(max_retries=s.source_max_retries)
    )
    mcp = FastMCP(
        SERVER_NAME,
        instructions="Ticketing tools: search similar historical incidents, list open tickets, and create approved tickets.",
        host=s.ticketing_mcp_host,
        port=s.ticketing_mcp_port,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def run(tool: str, ctx: Context | None, body: Any) -> Any:
        return await run_tool(SERVER_NAME, tool, ctx, body, timeout_s=s.tool_timeout_s)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "server": SERVER_NAME})

    @mcp.tool(title="Search similar tickets", annotations=READ_ONLY)
    async def search_similar_tickets(
        query: Annotated[str, Field(min_length=2, max_length=2000, description="Free-text description of the issue")],
        asset_id: Annotated[str | None, Field(pattern=r"^AST-[A-Z]{3}-\d{3}$")] = None,
        asset_type: str | None = None,
        alarm_name: str | None = None,
        statuses: list[TicketStatus] | None = None,
        limit: Annotated[int, Field(ge=1, le=25)] = 5,
        ctx: Context | None = None,
    ) -> SimilarOutput:
        """Find historical incidents similar to an alarm (text similarity boosted by same asset / alarm / asset type). Includes resolution notes."""

        async def body(rec: CallRecorder) -> SimilarOutput:
            req = {"query": query, "asset_id": asset_id, "asset_type": asset_type, "alarm_name": alarm_name, "statuses": statuses, "limit": limit}
            data = await rec.call(api.search_similar({k: v for k, v in req.items() if v is not None}))
            return SimilarOutput(results=data["results"], total_candidates=data["total_candidates"], trace_id=_tid(), source_calls=rec.calls)

        return await run("search_similar_tickets", ctx, body)

    @mcp.tool(title="List tickets", annotations=READ_ONLY)
    async def list_tickets(
        asset_ids: Annotated[list[str] | None, Field(max_length=25)] = None,
        statuses: list[TicketStatus] | None = None,
        site: str | None = None,
        max_pages: Annotated[int, Field(ge=1, le=10)] = 3,
        ctx: Context | None = None,
    ) -> TicketListOutput:
        """List tickets filtered by assets and status (e.g. open tickets on correlated assets). Follows pagination."""

        async def body(rec: CallRecorder) -> TicketListOutput:
            page, out, pages = 1, [], 0
            total = 0
            while pages < max_pages:
                data = await rec.call(
                    api.list_tickets(asset_ids=asset_ids, statuses=[str(x) for x in statuses] if statuses else None, site=site, page=page, page_size=50)
                )
                out.extend(data["data"])
                total = data["pagination"]["total"]
                pages += 1
                if not data["pagination"]["has_next"]:
                    break
                page += 1
            return TicketListOutput(tickets=out, total=total, pages_fetched=pages, trace_id=_tid(), source_calls=rec.calls)

        return await run("list_tickets", ctx, body)

    @mcp.tool(title="Get ticket", annotations=READ_ONLY)
    async def get_ticket(ticket_id: Annotated[str, Field(pattern=r"^INC-\d{4,8}$")], ctx: Context | None = None) -> TicketOutput:
        """Fetch one ticket by ID."""

        async def body(rec: CallRecorder) -> TicketOutput:
            data = await rec.call(api.get_ticket(ticket_id))
            return TicketOutput(ticket=data, trace_id=_tid(), source_calls=rec.calls)

        return await run("get_ticket", ctx, body)

    @mcp.tool(title="Create ticket (requires approval)", annotations=WRITE)
    async def create_ticket(
        draft: TicketDraft,
        approval_token: Annotated[str, Field(min_length=10, description="Token minted by the copilot when a human approved this exact draft")],
        approved_by: Annotated[str, Field(min_length=1, max_length=120)],
        idempotency_key: Annotated[str, Field(min_length=8, max_length=80)],
        ctx: Context | None = None,
    ) -> TicketOutput:
        """WRITE OPERATION. Create an incident ticket. Rejected unless approval_token was issued for exactly this draft by a human approver."""
        payload = draft.model_dump(mode="json")
        try:
            digest = approval.verify(approval_token, payload, s.approval_signing_key)
        except approval.ApprovalError as exc:
            raise tool_error("approval_required", f"Ticket creation blocked: {exc}") from exc

        async def body(rec: CallRecorder) -> TicketOutput:
            req = {**payload, "approval": {"approved_by": approved_by, "approved_at": datetime.now(UTC).isoformat(), "draft_hash": digest}}
            data = await rec.call(api.create_ticket(req, idempotency_key))
            replay = bool(data.pop("idempotent_replay", False))
            return TicketOutput(ticket=data, idempotent_replay=replay, trace_id=_tid(), source_calls=rec.calls)

        return await run("create_ticket", ctx, body)

    @mcp.tool(title="Add ticket comment (requires approval)", annotations=WRITE)
    async def add_ticket_comment(
        ticket_id: Annotated[str, Field(pattern=r"^INC-\d{4,8}$")],
        comment: Annotated[str, Field(min_length=1, max_length=5000)],
        author: Annotated[str, Field(min_length=1, max_length=120)],
        approval_token: str,
        ctx: Context | None = None,
    ) -> CommentOutput:
        """WRITE OPERATION. Append a comment to an existing ticket. Requires a human approval token bound to ticket_id + comment."""
        try:
            approval.verify(approval_token, {"ticket_id": ticket_id, "comment": comment}, s.approval_signing_key)
        except approval.ApprovalError as exc:
            raise tool_error("approval_required", f"Comment blocked: {exc}") from exc

        async def body(rec: CallRecorder) -> CommentOutput:
            data = await rec.call(api.add_comment(ticket_id, author, comment))
            return CommentOutput(ticket_id=ticket_id, comment=data, trace_id=_tid(), source_calls=rec.calls)

        return await run("add_ticket_comment", ctx, body)

    return mcp
