#!/usr/bin/env bash
# Start the full stack locally without Docker: simulators, both MCP servers, backend+GUI.
# Usage: scripts/run_local.sh        (Ctrl+C stops everything)
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-.venv/bin/python}"
export PYTHONPATH=".:apps/backend:mcp-servers/alarm-management:mcp-servers/ticketing"
set -a; [ -f .env ] && . ./.env; set +a

export ALARM_API_TOKEN="${ALARM_API_TOKEN:-demo-token}"
export TICKETING_API_TOKEN="${TICKETING_API_TOKEN:-ticket-demo-token}"
export ALARM_API_BASE_URL="${ALARM_API_BASE_URL:-http://127.0.0.1:8000}"
export TICKETING_API_URL="${TICKETING_API_URL:-http://127.0.0.1:8100}"
export ALARM_MCP_URL="${ALARM_MCP_URL:-http://127.0.0.1:9001/mcp}"
export TICKETING_MCP_URL="${TICKETING_MCP_URL:-http://127.0.0.1:9002/mcp}"
export ALARM_MCP_TOKEN="${ALARM_MCP_TOKEN:-local-alarm-mcp-token}"
export TICKETING_MCP_TOKEN="${TICKETING_MCP_TOKEN:-local-ticketing-mcp-token}"
export APPROVAL_SIGNING_KEY="${APPROVAL_SIGNING_KEY:-local-approval-signing-key}"

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

"$PY" -m uvicorn simulators.alarm_api_sim.app:app --host 127.0.0.1 --port 8000 --log-level warning & pids+=($!)
"$PY" -m uvicorn simulators.ticketing_api_sim.app:app --host 127.0.0.1 --port 8100 --log-level warning & pids+=($!)
"$PY" -m alarm_mcp & pids+=($!)
"$PY" -m ticketing_mcp & pids+=($!)
sleep 2
"$PY" -m rag.ingestion.ingest --docs rag/documents --index rag/index/index.json >/dev/null
COPILOT_HOST=127.0.0.1 COPILOT_PORT=8080 "$PY" -m copilot & pids+=($!)

echo "GUI:            http://127.0.0.1:8080"
echo "Alarm API sim:  http://127.0.0.1:8000/docs"
echo "Ticketing sim:  http://127.0.0.1:8100/docs"
echo "Alarm MCP:      http://127.0.0.1:9001/mcp   Ticketing MCP: http://127.0.0.1:9002/mcp"
wait
