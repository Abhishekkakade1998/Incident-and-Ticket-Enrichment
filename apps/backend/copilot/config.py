"""Copilot backend configuration (environment variables / .env)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # MCP servers (the copilot only talks to these, never to source APIs directly)
    alarm_mcp_url: str = "http://localhost:9001/mcp"
    alarm_mcp_token: str | None = None
    ticketing_mcp_url: str = "http://localhost:9002/mcp"
    ticketing_mcp_token: str | None = None
    mcp_tool_timeout_s: float = 30.0

    # RAG
    document_path: Path = REPO_ROOT / "rag" / "documents"
    rag_index_path: Path = REPO_ROOT / "rag" / "index" / "index.json"
    rag_auto_ingest: bool = True
    rag_top_k: int = 5

    # LLM (replaceable provider; "offline" = deterministic grounded composer, no external calls)
    llm_provider: str = Field(default="offline", description="offline | anthropic")
    llm_api_key: str | None = None
    llm_model: str = "claude-opus-5-5"
    llm_timeout_s: float = 60.0

    # Write-approval
    approval_signing_key: str = "change-me-approval-key"
    approval_ttl_s: int = 600

    # Persistence / ops
    audit_db_path: Path = REPO_ROOT / "data" / "audit.sqlite3"
    frontend_dir: Path = REPO_ROOT / "apps" / "frontend"
    cors_origins: list[str] = ["http://localhost:8080", "http://localhost:3000"]
    log_level: str = "INFO"
    default_lookback_days: int = 90


@lru_cache
def get_settings() -> Settings:
    return Settings()
