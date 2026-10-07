#!/usr/bin/env bash
# Start the web dashboard in Docker with your GitHub token passed in from `gh` (the token is never printed).
# Usage: scripts/web-up.sh [--bridge] [docker compose up options, e.g. -d]
#   Host networking is the default (needed on a VPN, where a GitHub Enterprise API is not reachable from
#   Docker's bridge network). --bridge uses the isolated bridge network and a published port instead.
#   Set TOKEN_MONITOR_SOURCE=mock to run on purpose with sample data (no token needed).
set -euo pipefail
cd "$(dirname "$0")/.."

host="${TOKEN_MONITOR_GH_HOST:-}"
if [ -z "$host" ] && [ -f .env ]; then      # same default the dashboard will use: the host named in .env
  host="$(grep -E '^[[:space:]]*(export[[:space:]]+)?TOKEN_MONITOR_GH_HOST=' .env | tail -1 | cut -d= -f2- | sed -E "s/[[:space:]]+#.*//; s/^['\"]//; s/['\"]$//")"
fi
host="${host:-github.com}"
if [ -z "${TOKEN_MONITOR_GH_TOKEN:-}" ]; then
  if command -v gh >/dev/null 2>&1 && token="$(gh auth token -h "$host" 2>/dev/null)" && [ -n "$token" ]; then
    export TOKEN_MONITOR_GH_TOKEN="$token"
  else
    if [ "${TOKEN_MONITOR_SOURCE:-}" != "mock" ]; then
      echo "No GitHub token found (gh is missing or not logged in to $host)." >&2
      echo "Run 'gh auth login -h $host' and try again (or TOKEN_MONITOR_SOURCE=mock for sample data)." >&2
      exit 1
    fi
  fi
fi
export TOKEN_MONITOR_GH_HOST="$host"
export HOST_UID="$(id -u)" HOST_GID="$(id -g)"
export TZ="${TZ:-$(cat /etc/timezone 2>/dev/null || echo UTC)}"

mkdir -p .token-monitor        # shared with the container (history, offline fallback); created as you, not root
files=(-f docker-compose.yml -f docker-compose.host-network.yml)
args=()
for arg in "$@"; do
  case "$arg" in
    --bridge) files=(-f docker-compose.yml) ;;
    --host-network) ;;          # the default; kept so old command lines still work
    *) args+=("$arg") ;;
  esac
done
export TOKEN_MONITOR_PORT="${TOKEN_MONITOR_PORT:-8080}"
exec docker compose "${files[@]}" up --build "${args[@]}"
