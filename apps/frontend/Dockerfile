# Static GUI served by nginx; /api, /ready and /health are proxied to the copilot backend.
FROM nginx:1.27-alpine
COPY apps/frontend/docker/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY apps/frontend/docker/docker-entrypoint-config.sh /docker-entrypoint.d/40-copilot-config.sh
COPY apps/frontend/index.html /usr/share/nginx/html/index.html
COPY apps/frontend/app.js apps/frontend/styles.css apps/frontend/config.js /usr/share/nginx/html/static/
RUN chmod +x /docker-entrypoint.d/40-copilot-config.sh
EXPOSE 80
