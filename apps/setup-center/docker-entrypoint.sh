#!/bin/sh
set -e

# Substitute BACKEND_HOST and BACKEND_PORT into the nginx config template
envsubst '${BACKEND_HOST} ${BACKEND_PORT}' \
  < /etc/nginx/templates/default.conf.template \
  > /etc/nginx/conf.d/default.conf

echo "[entrypoint] nginx upstream → ${BACKEND_HOST}:${BACKEND_PORT}"

exec nginx -g "daemon off;"
