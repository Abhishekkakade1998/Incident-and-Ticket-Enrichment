# One image for every Python service (simulators, MCP servers, RAG ingestion, copilot backend).
# The service is selected by the compose `command`.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/apps/backend:/app/mcp-servers/alarm-management:/app/mcp-servers/ticketing

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY common ./common
COPY connectors ./connectors
COPY simulators ./simulators
COPY mcp-servers ./mcp-servers
COPY rag ./rag
COPY apps/backend ./apps/backend
COPY apps/frontend ./apps/frontend

RUN useradd --create-home --uid 10001 app && mkdir -p /app/data /app/rag/index && chown -R app /app/data /app/rag/index
USER app

EXPOSE 8000 8080 8100 9001 9002
CMD ["python", "-m", "copilot"]
