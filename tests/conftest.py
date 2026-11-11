"""Shared fixtures: the whole stack wired in-process.

Simulator (FastAPI) <-ASGI transport- connectors <- MCP servers <-in-memory MCP- registry
<- orchestrator. Only the network hops are replaced; every MCP request goes through the
real MCP protocol (JSON-RPC over memory streams) and every API call through real HTTP
handling in the simulators.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

os.environ.setdefault("SIM_ANCHOR_TIME", "2026-11-10T12:00:00Z")

from alarm_mcp.server import AlarmMcpSettings
from alarm_mcp.server import build_server as build_alarm_server
from connectors.alarm_api import AlarmApiClient
from connectors.http import RetryPolicy
from connectors.ticketing_api import TicketingClient
from copilot.audit import AuditLog
from copilot.llm import OfflineProvider
from copilot.mcp_client import InMemoryMcpConnection, ToolRegistry
from copilot.orchestrator import Orchestrator
from copilot.state import ConversationStore
from rag.ingestion.ingest import ingest
from rag.retrieval.retriever import Retriever
from simulators.alarm_api_sim.app import create_app as create_alarm_sim
from simulators.ticketing_api_sim.app import create_app as create_ticket_sim
from ticketing_mcp.server import TicketingMcpSettings
from ticketing_mcp.server import build_server as build_ticket_server

ROOT = Path(__file__).resolve().parents[1]
ANCHOR = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
APPROVAL_KEY = "test-approval-key"
FAST_RETRY = RetryPolicy(max_retries=2, backoff_base_s=0.01, backoff_max_s=0.02)


@pytest.fixture(scope="session")
def alarm_sim_app():
    return create_alarm_sim(token="demo-token", anchor=ANCHOR)


@pytest.fixture
def ticket_sim_app():
    return create_ticket_sim(token="ticket-demo-token")


@pytest.fixture
def alarm_client(alarm_sim_app) -> AlarmApiClient:
    return AlarmApiClient("http://alarm-sim", "demo-token", transport=httpx.ASGITransport(app=alarm_sim_app), retry=FAST_RETRY)


@pytest.fixture
def ticket_client(ticket_sim_app) -> TicketingClient:
    return TicketingClient("http://ticket-sim", "ticket-demo-token", transport=httpx.ASGITransport(app=ticket_sim_app), retry=FAST_RETRY)


@pytest.fixture
def alarm_mcp(alarm_client):
    return build_alarm_server(AlarmMcpSettings(tool_timeout_s=10), client=alarm_client)


@pytest.fixture
def ticket_mcp(ticket_client):
    return build_ticket_server(TicketingMcpSettings(approval_signing_key=APPROVAL_KEY), client=ticket_client)


@pytest.fixture(scope="session")
def index_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("rag") / "index.json"
    ingest(ROOT / "rag" / "documents", path)
    return path


@pytest.fixture(scope="session")
def retriever(index_path) -> Retriever:
    return Retriever.from_path(index_path)


@pytest.fixture
async def registry(alarm_mcp, ticket_mcp) -> AsyncIterator[ToolRegistry]:
    reg = ToolRegistry([InMemoryMcpConnection("alarm-management", alarm_mcp), InMemoryMcpConnection("ticketing", ticket_mcp)], default_timeout_s=15)
    await reg.discover()
    yield reg


@pytest.fixture
def audit() -> Iterator[AuditLog]:
    yield AuditLog(":memory:")


@pytest.fixture
def orchestrator(registry, retriever, audit) -> Orchestrator:
    return Orchestrator(registry, retriever, OfflineProvider(), ConversationStore(), audit)
