#!/usr/bin/env bash
# Checks that the host ports this lab wants are free. Reads overrides from .env.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
status=0
for entry in "POSTGRES_HOST_PORT:${POSTGRES_HOST_PORT:-55432}" "API_PORT:${API_PORT:-8000}" "WEB_PORT:${WEB_PORT:-3000}"; do
  name="${entry%%:*}"; port="${entry##*:}"
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "BUSY  $name=$port  ($(lsof -nP -iTCP:"$port" -sTCP:LISTEN | awk 'NR==2{print $1}'))"
    status=1
  else
    echo "free  $name=$port"
  fi
done
[ $status -ne 0 ] && echo "Change the busy ports in .env (a running lab of this project will also show as busy)."
exit $status
