"""Entry point: ``python -m alarm_mcp [--transport streamable-http|stdio]``."""

from __future__ import annotations

import argparse

import uvicorn

from alarm_mcp.server import AlarmMcpSettings, build_server
from common.logging import configure_logging
from common.mcp_support import BearerAuthMiddleware


def main() -> None:
    parser = argparse.ArgumentParser(description="Alarm Management MCP server")
    parser.add_argument("--transport", choices=["streamable-http", "stdio"], default="streamable-http")
    args = parser.parse_args()
    settings = AlarmMcpSettings()
    configure_logging("alarm-mcp", settings.log_level)
    server = build_server(settings)
    if args.transport == "stdio":
        server.run("stdio")
        return
    app = BearerAuthMiddleware(server.streamable_http_app(), settings.alarm_mcp_token)
    uvicorn.run(app, host=settings.alarm_mcp_host, port=settings.alarm_mcp_port, log_config=None)


if __name__ == "__main__":
    main()
