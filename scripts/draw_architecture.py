"""Render docs/architecture-diagram.png (requires matplotlib: pip install matplotlib)."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(__file__).resolve().parents[1] / "docs" / "architecture-diagram.png"

C = {
    "ui": "#dbe7fb",
    "orch": "#e8e2f7",
    "mcp": "#d6efe0",
    "src": "#fde9cf",
    "rag": "#f3e1f6",
    "obs": "#eceff3",
    "edge": "#2c3443",
    "mcp_line": "#1f7a46",
    "rag_line": "#7a3fa0",
    "auth": "#b42318",
}

fig, ax = plt.subplots(figsize=(16, 10), dpi=150)
ax.set_xlim(0, 160)
ax.set_ylim(0, 100)
ax.axis("off")


def box(x, y, w, h, title, lines=(), color="#ffffff", bold=True):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.5", fc=color, ec=C["edge"], lw=1.2))
    ax.text(x + w / 2, y + h - 2.6, title, ha="center", va="top", fontsize=10.5, weight="bold" if bold else "normal", color="#111")
    for i, ln in enumerate(lines):
        ax.text(x + w / 2, y + h - 6.2 - i * 2.9, ln, ha="center", va="top", fontsize=8, color="#333")


def arrow(x1, y1, x2, y2, label="", color=C["edge"], style="-|>", ls="-", lw=1.4, lx=0, ly=0):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style, mutation_scale=12, color=color, lw=lw, linestyle=ls))
    if label:
        ax.text(
            (x1 + x2) / 2 + lx,
            (y1 + y2) / 2 + ly,
            label,
            fontsize=7.5,
            color=color,
            ha="center",
            va="center",
            bbox={"boxstyle": "round,pad=0.2", "fc": "white", "ec": "none", "alpha": 0.9},
        )


def zone(x, y, w, h, label):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.2,rounding_size=2", fc="none", ec=C["auth"], lw=1.1, ls=(0, (5, 3))))
    ax.text(x + 1.2, y + h - 1.2, label, fontsize=7.5, color=C["auth"], va="top", style="italic")


ax.text(
    80, 98.5, "Incident & Ticket Enrichment Copilot - architecture (MCP path in green, RAG path in purple)", ha="center", va="top", fontsize=13, weight="bold"
)

# --- GUI
box(4, 78, 34, 14, "GUI (apps/frontend)", ["Chat · ticket draft editor + approval", "Similar tickets · citations", "MCP trace · tool catalog · audit"], C["ui"])

# --- Copilot backend
zone(44, 43, 72, 51, "trust zone: copilot backend (holds MCP tokens + approval key, never source-API tokens)")
box(48, 77, 30, 13, "API layer (FastAPI)", ["/api/chat · /api/tools · /api/drafts", "trace middleware · input validation"], C["orch"])
box(82, 76, 30, 15, "Orchestrator", ["intent + entities -> plan", "multi-step workflows", "partial-failure handling"], C["orch"])
box(48, 59, 30, 15, "Evidence composer", ["cause ranking · priority reconcile", "citations [T#] [D#] [INC-#]", "ticket draft builder"], C["orch"])
box(82, 59, 30, 15, "LLM provider (optional)", ["offline composer (default)", "Claude via Anthropic SDK", "citation-validated output"], C["orch"])
box(48, 45, 30, 11, "MCP client / tool registry", ["discovery · JSON-Schema validation", "timeouts · error records"], C["mcp"])
box(82, 45, 30, 11, "Retriever (RAG)", ["BM25 + vector, RRF fusion", "filters · trust · confidence"], C["rag"])

# --- MCP servers
zone(4, 22, 66, 20, "trust zone: MCP servers (bearer-token protected; hold source-system tokens)")
box(7, 24, 29, 15, "Alarm Management MCP server", ["14 typed tools (read-only)", "retry · timeout · error mapping", "pagination · trace propagation"], C["mcp"])
box(39, 24, 28, 15, "Ticketing MCP server", ["similar / list / get tickets", "create_ticket: HMAC approval", "+ idempotency key"], C["mcp"])

# --- Source systems
box(7, 4, 29, 13, "Alarm Management API", ["simulator (Postman contract)", "bearer auth · trace_id echo"], C["src"])
box(39, 4, 28, 13, "Ticketing API", ["mock ServiceNow-style", "Idempotency-Key on create"], C["src"])

# --- RAG ingestion
zone(76, 3, 80, 37, "RAG pipeline")
box(80, 22, 34, 14, "Document store", ["rag/documents: SOPs, guides,", "knowledge articles, resolution", "notes, escalation (.md/.txt/.html/.json)"], C["rag"])
box(118, 22, 35, 14, "Ingestion pipeline", ["extract -> sanitise (injection) ->", "chunk (heading-aware) ->", "embed -> write index"], C["rag"])
box(98, 5, 36, 12, "Retrieval index", ["rag/index/index.json", "chunks + vectors + metadata"], C["rag"])

# --- Observability
box(
    120,
    62,
    36,
    28,
    "Observability",
    [
        "structured JSON logs (redacted)",
        "trace_id · request_id · conv id",
        "mcp_server · mcp_tool · duration",
        "API status · retry_count",
        "retrieval query · doc ids · scores",
        "LLM latency",
        "SQLite audit trail",
    ],
    C["obs"],
)

# --- arrows: user flow
arrow(38, 85, 48, 85, "HTTPS JSON")
arrow(78, 83, 82, 83)
arrow(90, 76, 70, 74, "compose", ly=1.5)
arrow(104, 76, 104, 74, "")
# MCP path
arrow(84, 76, 70, 56, "tool calls", color=C["mcp_line"], lx=-3)
arrow(56, 45, 24, 39, "Streamable HTTP + Bearer\n_meta trace_id", color=C["mcp_line"], lw=2, lx=-4)
arrow(66, 45, 54, 39, "", color=C["mcp_line"], lw=2)
arrow(21, 24, 21, 17, "HTTP + Bearer\ntrace headers", color=C["mcp_line"], lw=2, lx=-1)
arrow(53, 24, 53, 17, "HTTP + Bearer", color=C["mcp_line"], lw=2, lx=-1)
# RAG path
arrow(110, 76, 104, 56, "retrieve", color=C["rag_line"], lx=5)
arrow(114, 29, 118, 29, "", color=C["rag_line"], lw=2)
arrow(135, 22, 122, 17, "build / refresh", color=C["rag_line"], lw=2, lx=6)
arrow(130, 17, 110, 45, "load", color=C["rag_line"], lw=2, lx=4)
# approval
arrow(21, 78, 54, 39, "approve draft -> HMAC token -> create_ticket", color=C["auth"], ls="--", lx=-8, ly=6)
# observability
arrow(116, 72, 120, 74, "", color="#777", ls=":")

ax.text(
    4,
    1,
    "Dashed red boxes = authentication / trust boundaries. Retrieved documents are untrusted data: sanitised at ingestion, fenced in prompts, citation-validated.",
    fontsize=8,
    color="#555",
)
OUT.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(OUT, bbox_inches="tight", facecolor="white")
print(f"wrote {OUT}")
