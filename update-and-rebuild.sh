#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

APP_PORT=${APP_PORT_BIND:-18000}
SOLVER_URL=${SOLVER_URL:-http://127.0.0.1:${APP_PORT}/api/solver/status}
HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:${APP_PORT}/api/health}
READY_URL=${READY_URL:-$HEALTH_URL}

echo "==> repo: $SCRIPT_DIR"
echo "==> git fetch --all --prune"
git fetch --all --prune

echo "==> git pull --ff-only"
git pull --ff-only

echo "==> docker compose config"
docker compose config >/dev/null

echo "==> docker compose up -d --build"
docker compose up -d --build

echo "==> docker compose ps"
docker compose ps

echo "==> waiting for readiness"
i=0
while [ "$i" -lt 60 ]; do
  if curl -fsS "$READY_URL" | grep -q '"ok":true'; then
    printf 'ready: %s\n' "$READY_URL"
    break
  fi
  i=$((i + 1))
  sleep 2
done

if [ "$i" -ge 60 ]; then
  echo "readiness check timeout: $READY_URL" >&2
  docker compose logs --tail=120 app || true
  exit 1
fi

echo "==> checking solver"
i=0
while [ "$i" -lt 30 ]; do
  if curl -fsS "$SOLVER_URL" | grep -q '"running":true'; then
    printf 'solver ready: %s\n' "$SOLVER_URL"
    exit 0
  fi
  i=$((i + 1))
  sleep 2
done

echo "solver check timeout: $SOLVER_URL" >&2
docker compose logs --tail=120 app || true
exit 1