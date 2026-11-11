"""Mock ticketing API (ServiceNow-like incident table) used as the secondary source system.

* Seeded historical incidents with resolution notes, for similar-ticket search.
* Open incidents on a few assets, for "open tickets linked to correlated assets".
* ``POST /tickets`` requires an ``Idempotency-Key`` header and an ``approval`` block;
  replaying the same key returns the original ticket instead of creating a duplicate.
"""

from __future__ import annotations

import math
import os
import re
import secrets
from collections import Counter
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

Status = Literal["open", "in_progress", "resolved", "closed"]
Priority = Literal["P1", "P2", "P3", "P4"]

# (asset_id, asset_name, asset_type, site, alarm_name, title, description, resolution, status, priority, created)
SEED: list[tuple[str, str, str, str, str, str, str, str, str, str, str]] = [
    (
        "AST-BFP-101",
        "Boiler Feed Pump 101",
        "pump",
        "NorthPlant",
        "High Vibration",
        "BFP-101 high vibration and bearing temperature after low suction pressure",
        "Repeated high vibration on P-101 following low suction pressure alarms during load ramp.",
        "Suction strainer found 60% blocked; cleaned strainer and restored NPSH margin. Vibration back to 2.1 mm/s.",
        "closed",
        "P2",
        "2026-03-14T08:20:00Z",
    ),
    (
        "AST-BFP-101",
        "Boiler Feed Pump 101",
        "pump",
        "NorthPlant",
        "High Bearing Temperature",
        "P-101 NDE bearing temperature high",
        "NDE bearing reached 91 degC with concurrent vibration rise.",
        "Lube oil degraded (water ingress). Oil changed, breather replaced. Recommend oil analysis monthly.",
        "closed",
        "P2",
        "2026-05-02T13:05:00Z",
    ),
    (
        "AST-BFP-101",
        "Boiler Feed Pump 101",
        "pump",
        "NorthPlant",
        "Low Suction Pressure",
        "Recurring low suction pressure on BFP-101",
        "Low suction pressure alarm recurring every 3-5 days; deaerator level normal.",
        "Min-flow recirculation valve FV-101 found sticking at 20%. Positioner recalibrated.",
        "resolved",
        "P3",
        "2026-07-21T06:40:00Z",
    ),
    (
        "AST-BFP-101",
        "Boiler Feed Pump 101",
        "pump",
        "NorthPlant",
        "Seal Leak Detected",
        "BFP-101 mechanical seal leak",
        "Seal leak detection after a cavitation episode. Hot feedwater dripping at seal gland.",
        "Mechanical seal replaced under permit. Root cause: dry-running damage from cavitation.",
        "closed",
        "P1",
        "2026-08-30T22:10:00Z",
    ),
    (
        "AST-BFP-102",
        "Boiler Feed Pump 102",
        "pump",
        "NorthPlant",
        "Low Suction Pressure",
        "BFP-102 low suction pressure while standby pump in service",
        "Low suction pressure on P-102 when started as standby for P-101.",
        "",
        "open",
        "P2",
        "2026-11-09T10:15:00Z",
    ),
    (
        "AST-BFP-102",
        "Boiler Feed Pump 102",
        "pump",
        "NorthPlant",
        "Motor Overcurrent",
        "P-102 motor overcurrent on start",
        "Motor current spike on start, settles after 20 s.",
        "Soft-starter ramp time increased from 5 s to 12 s.",
        "closed",
        "P3",
        "2026-04-11T09:00:00Z",
    ),
    (
        "AST-DEA-110",
        "Deaerator D-110",
        "vessel",
        "NorthPlant",
        "Low Level",
        "Deaerator low level during feedwater upset",
        "D-110 low level alarm coincident with feed pump changeover.",
        "Level controller retuned; make-up valve stroke verified.",
        "closed",
        "P3",
        "2026-06-03T04:30:00Z",
    ),
    (
        "AST-CMP-501",
        "Gas Compressor K-501",
        "compressor",
        "EastRefinery",
        "Surge Detected",
        "K-501 surge events during feed gas reduction",
        "Three surge events in 12 h when upstream unit reduced feed gas rate.",
        "Anti-surge valve UV-501 found slow to open (stroke 9 s vs 2 s spec). Actuator solenoid replaced.",
        "closed",
        "P1",
        "2026-02-18T15:45:00Z",
    ),
    (
        "AST-CMP-501",
        "Gas Compressor K-501",
        "compressor",
        "EastRefinery",
        "High Discharge Temperature",
        "K-501 discharge temperature high after surge recovery",
        "Discharge temperature rose to 172 degC following recycle operation.",
        "Aftercooler E-501 fouled on cooling water side. Cleaned during planned outage.",
        "closed",
        "P2",
        "2026-05-27T11:20:00Z",
    ),
    (
        "AST-CMP-501",
        "Gas Compressor K-501",
        "compressor",
        "EastRefinery",
        "Surge Detected",
        "Recurring surge on K-501 - anti-surge controller tuning",
        "Surge detection repeated weekly; controller margin set to 5%.",
        "Surge control margin increased to 10%; controller gain retuned with OEM.",
        "resolved",
        "P2",
        "2026-09-12T07:55:00Z",
    ),
    (
        "AST-CMP-501",
        "Gas Compressor K-501",
        "compressor",
        "EastRefinery",
        "Surge Detected",
        "K-501 surge alarm with motor overcurrent on M-501",
        "Surge on K-501 with simultaneous overcurrent on drive motor M-501. Investigating.",
        "",
        "in_progress",
        "P1",
        "2026-11-08T19:30:00Z",
    ),
    (
        "AST-MTR-501",
        "Compressor Drive Motor M-501",
        "motor",
        "EastRefinery",
        "Motor Overcurrent",
        "M-501 overcurrent trips coincide with compressor surge",
        "Motor overcurrent alarms on M-501 during K-501 surge events.",
        "",
        "open",
        "P2",
        "2026-11-06T14:00:00Z",
    ),
    (
        "AST-MTR-501",
        "Compressor Drive Motor M-501",
        "motor",
        "EastRefinery",
        "High Winding Temperature",
        "M-501 winding temperature high",
        "Winding RTD reading 134 degC on hot day.",
        "Motor cooling air filter replaced; RTD verified.",
        "closed",
        "P3",
        "2026-07-02T16:10:00Z",
    ),
    (
        "AST-CMP-502",
        "Recycle Compressor K-502",
        "compressor",
        "EastRefinery",
        "High Vibration",
        "K-502 high vibration - suspected coupling misalignment",
        "Vibration 7.8 mm/s at 1x and 2x running speed.",
        "Laser alignment performed; coupling shims corrected. Vibration 2.4 mm/s.",
        "closed",
        "P2",
        "2026-04-29T10:00:00Z",
    ),
    (
        "AST-CMP-502",
        "Recycle Compressor K-502",
        "compressor",
        "EastRefinery",
        "High Vibration",
        "K-502 vibration trending up again",
        "Vibration rising slowly over two weeks, now 6.9 mm/s.",
        "",
        "open",
        "P3",
        "2026-11-03T08:45:00Z",
    ),
    (
        "AST-CMP-503",
        "Instrument Air Compressor K-503",
        "compressor",
        "EastRefinery",
        "High Interstage Pressure",
        "K-503 interstage pressure high",
        "Interstage pressure high alarm; intercooler drain trap suspected.",
        "Intercooler condensate trap replaced.",
        "closed",
        "P3",
        "2026-06-19T12:00:00Z",
    ),
    (
        "AST-CWP-201",
        "Cooling Water Pump 201",
        "pump",
        "NorthPlant",
        "High Vibration",
        "CWP-201 vibration after impeller replacement",
        "Vibration 6.5 mm/s after impeller change.",
        "Impeller dynamically balanced; vibration acceptable.",
        "closed",
        "P3",
        "2026-03-30T09:30:00Z",
    ),
    (
        "AST-HEX-210",
        "Heat Exchanger E-210",
        "heat_exchanger",
        "NorthPlant",
        "Fouling Indicator",
        "E-210 fouling indicator standing alarm",
        "Fouling indicator has been standing for weeks; nuisance alarm.",
        "",
        "open",
        "P4",
        "2026-10-15T07:00:00Z",
    ),
    (
        "AST-STT-301",
        "Steam Turbine T-301",
        "turbine",
        "SouthPlant",
        "High Exhaust Temperature",
        "T-301 exhaust temperature high at part load",
        "Exhaust temperature high during low-load operation.",
        "Operating procedure updated: avoid sustained operation below 30% load.",
        "closed",
        "P2",
        "2026-05-08T21:00:00Z",
    ),
    (
        "AST-FGS-401",
        "Fuel Gas Skid FG-401",
        "skid",
        "SouthPlant",
        "Low Fuel Gas Pressure",
        "FG-401 low fuel gas pressure",
        "Fuel gas pressure dipping below 2 barg; filter DP high.",
        "Coalescing filter element replaced.",
        "closed",
        "P2",
        "2026-08-14T05:45:00Z",
    ),
]

ASSET_TYPES = {s[0]: s[2] for s in SEED}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentLink(StrictModel):
    doc_id: str = Field(max_length=80)
    title: str = Field(max_length=200)
    section: str | None = Field(default=None, max_length=200)


class Approval(StrictModel):
    approved_by: str = Field(min_length=1, max_length=120)
    approved_at: datetime
    draft_hash: str = Field(min_length=16, max_length=128)


class TicketCreate(StrictModel):
    title: str = Field(min_length=5, max_length=200)
    description: str = Field(min_length=10, max_length=20000)
    priority: Priority
    asset_id: str = Field(max_length=40)
    asset_name: str | None = Field(default=None, max_length=120)
    site: str | None = Field(default=None, max_length=60)
    alarm_ids: list[str] = Field(default_factory=list, max_length=50)
    alarm_name: str | None = Field(default=None, max_length=120)
    category: str = Field(default="alarm_incident", max_length=60)
    assignee_group: str | None = Field(default=None, max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=20)
    linked_documents: list[DocumentLink] = Field(default_factory=list, max_length=20)
    similar_ticket_ids: list[str] = Field(default_factory=list, max_length=20)
    approval: Approval


class SimilarRequest(StrictModel):
    query: str = Field(min_length=2, max_length=2000)
    asset_id: str | None = None
    asset_type: str | None = None
    alarm_name: str | None = None
    statuses: list[Status] | None = None
    limit: int = Field(default=5, ge=1, le=25)


class CommentCreate(StrictModel):
    author: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=5000)


_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "on", "of", "and", "or", "to", "in", "for", "with", "after", "during", "is", "at", "by", "from"}


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


def _ticket_text(t: dict[str, Any]) -> str:
    return " ".join([t["title"], t["description"], t.get("alarm_name") or "", t.get("resolution_notes") or "", t.get("asset_name") or ""])


def create_app(token: str | None = None) -> FastAPI:
    api_token = token or os.environ.get("TICKETING_API_TOKEN", "ticket-demo-token")
    tickets: dict[str, dict[str, Any]] = {}
    idempotency: dict[str, str] = {}
    counter = {"n": 1000}

    def next_id() -> str:
        counter["n"] += 1
        return f"INC-{counter['n']}"

    for s in SEED:
        tid = next_id()
        tickets[tid] = {
            "ticket_id": tid,
            "title": s[5],
            "description": s[6],
            "status": s[8],
            "priority": s[9],
            "asset_id": s[0],
            "asset_name": s[1],
            "asset_type": s[2],
            "site": s[3],
            "alarm_name": s[4],
            "alarm_ids": [],
            "category": "alarm_incident",
            "assignee_group": "Rotating Equipment" if s[2] in ("pump", "compressor", "turbine") else "Reliability",
            "resolution_notes": s[7] or None,
            "created_at": s[10],
            "updated_at": s[10],
            "tags": [],
            "linked_documents": [],
            "comments": [],
        }

    app = FastAPI(title="Mock Ticketing API", version="1.0.0")

    def _err(status: int, code: str, msg: str, request: Request, details: Any = None) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={"error": {"code": code, "message": msg, "details": details}, "trace_id": request.headers.get("trace_id")},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _err(422, "validation_error", "Request failed validation", request, [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()])

    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict"}.get(exc.status_code, "error")
        return _err(exc.status_code, code, str(exc.detail), request)

    @app.middleware("http")
    async def _echo(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        for h in ("trace_id", "x-request-id"):
            if request.headers.get(h):
                response.headers[h] = request.headers[h]
        return response

    def require_auth(authorization: str | None = Header(default=None)) -> None:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "Missing bearer token")
        if not secrets.compare_digest(authorization.split(" ", 1)[1].strip(), api_token):
            raise HTTPException(403, "Invalid bearer token")

    auth = [Depends(require_auth)]

    def public(t: dict[str, Any]) -> dict[str, Any]:
        return dict(t)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "service": "ticketing-api-mock", "tickets": len(tickets)}

    @app.get("/tickets", dependencies=auth)
    async def list_tickets(
        asset_id: list[str] | None = Query(default=None),
        status: str | None = Query(default=None, description="Comma-separated statuses"),
        site: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        ids = {i for a in (asset_id or []) for i in a.split(",") if i}
        statuses = {s.strip() for s in status.split(",")} if status else None
        rows = [
            t
            for t in tickets.values()
            if (not ids or t["asset_id"] in ids) and (not statuses or t["status"] in statuses) and (not site or (t.get("site") or "").lower() == site.lower())
        ]
        rows.sort(key=lambda t: t["created_at"], reverse=True)
        total = len(rows)
        start = (page - 1) * page_size
        total_pages = max(1, -(-total // page_size))
        return {
            "data": [public(t) for t in rows[start : start + page_size]],
            "pagination": {"page": page, "page_size": page_size, "total": total, "total_pages": total_pages, "has_next": page < total_pages},
        }

    @app.get("/tickets/{ticket_id}", dependencies=auth)
    async def get_ticket(ticket_id: str) -> dict[str, Any]:
        t = tickets.get(ticket_id)
        if not t:
            raise HTTPException(404, f"Ticket {ticket_id} not found")
        return public(t)

    @app.post("/tickets/search-similar", dependencies=auth)
    async def search_similar(req: SimilarRequest) -> dict[str, Any]:
        docs = [t for t in tickets.values() if not req.statuses or t["status"] in req.statuses]
        tokenized = {t["ticket_id"]: _tokens(_ticket_text(t)) for t in docs}
        df: Counter[str] = Counter()
        for toks in tokenized.values():
            df.update(set(toks))
        n = max(1, len(docs))
        idf = {w: math.log(1 + n / c) for w, c in df.items()}

        def vec(toks: list[str]) -> dict[str, float]:
            tf = Counter(toks)
            return {w: c * idf.get(w, math.log(1 + n)) for w, c in tf.items()}

        q = vec(_tokens(req.query + " " + (req.alarm_name or "")))
        qn = math.sqrt(sum(v * v for v in q.values())) or 1.0
        results = []
        for t in docs:
            d = vec(tokenized[t["ticket_id"]])
            dn = math.sqrt(sum(v * v for v in d.values())) or 1.0
            cos = sum(q[w] * d.get(w, 0) for w in q) / (qn * dn)
            boost = 0.0
            reasons = []
            if req.asset_id and t["asset_id"] == req.asset_id:
                boost += 0.25
                reasons.append("same_asset")
            if req.alarm_name and (t.get("alarm_name") or "").lower() == req.alarm_name.lower():
                boost += 0.2
                reasons.append("same_alarm")
            if req.asset_type and t.get("asset_type") == req.asset_type:
                boost += 0.08
                reasons.append("same_asset_type")
            score = round(min(1.0, 0.6 * cos + boost), 3)
            if score >= 0.12:
                results.append({**public(t), "similarity": score, "match_reasons": reasons or ["text"]})
        results.sort(key=lambda r: -r["similarity"])
        return {"results": results[: req.limit], "total_candidates": len(docs)}

    @app.post("/tickets", dependencies=auth, status_code=201)
    async def create_ticket(
        req: TicketCreate,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Any:
        if not idempotency_key or len(idempotency_key) < 8:
            raise HTTPException(422, "Idempotency-Key header (>= 8 chars) is required for ticket creation")
        if idempotency_key in idempotency:
            existing = tickets[idempotency[idempotency_key]]
            return JSONResponse(status_code=200, content={**public(existing), "idempotent_replay": True})
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        tid = next_id()
        body = req.model_dump(mode="json")
        approval = body.pop("approval")
        tickets[tid] = {
            "ticket_id": tid,
            **body,
            "asset_type": ASSET_TYPES.get(req.asset_id),
            "status": "open",
            "resolution_notes": None,
            "created_at": now,
            "updated_at": now,
            "approval": approval,
            "comments": [],
        }
        idempotency[idempotency_key] = tid
        return public(tickets[tid])

    @app.post("/tickets/{ticket_id}/comments", dependencies=auth, status_code=201)
    async def add_comment(ticket_id: str, req: CommentCreate) -> dict[str, Any]:
        t = tickets.get(ticket_id)
        if not t:
            raise HTTPException(404, f"Ticket {ticket_id} not found")
        comment = {"author": req.author, "body": req.body, "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
        t["comments"].append(comment)
        t["updated_at"] = comment["created_at"]
        return comment

    return app


app = create_app()
