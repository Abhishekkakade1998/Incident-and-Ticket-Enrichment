"""Entry point: ``python -m ticketing_mcp [--transport streamable-http|stdio]``."""

from __future__ import annotations

import argparse

import uvicorn

from common.logging import configure_logging
from common.mcp_support import BearerAuthMiddleware
from ticketing_mcp.server import TicketingMcpSettings, build_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Ticketing MCP server")
    parser.add_argument("--transport", choices=["streamable-http", "stdio"], default="streamable-http")
    args = parser.parse_args()
    settings = TicketingMcpSettings()
    configure_logging("ticketing-mcp", settings.log_level)
    server = build_server(settings)
    if args.transport == "stdio":
        server.run("stdio")
        return
    app = BearerAuthMiddleware(server.streamable_http_app(), settings.ticketing_mcp_token)
    uvicorn.run(app, host=settings.ticketing_mcp_host, port=settings.ticketing_mcp_port, log_config=None)


if __name__ == "__main__":
    main()
