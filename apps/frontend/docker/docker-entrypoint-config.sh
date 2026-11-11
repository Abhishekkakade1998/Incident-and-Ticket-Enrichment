#!/bin/sh
# Generate the GUI runtime config from environment variables at container start.
set -eu
cat > /usr/share/nginx/html/static/config.js <<CFG
window.COPILOT_CONFIG = {
  apiBaseUrl: "${GUI_API_BASE_URL:-}",
  user: "${GUI_DEFAULT_USER:-operator}",
};
CFG
