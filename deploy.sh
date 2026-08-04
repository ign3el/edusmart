#!/usr/bin/env bash
# EduSmart deploy — builds a TAGGED image so rollback actually exists.
#
# Backend deploys blue/green (see PROJECT.md - "Zero-downtime deploys"):
# backend-blue and backend-green share one image (edusmart-backend:latest)
# and one job queue / spend-counter state (backend_db volume - concurrency
# safety verified: job_state.py's BEGIN IMMEDIATE, flock-protected usage
# counters). A deploy builds the image, starts it on whichever color is NOT
# currently live, health-checks THAT color directly (bypassing nginx),
# drains the old color's in-flight generations, and only then stops it.
# nginx (frontend/nginx.conf) already tolerates either color being down via
# its resolver+fallback setup, so this needs no nginx reload. If the new
# color fails its health check, the old one was never touched - no outage.
#
# Frontend still deploys in place (recreate, brief gap) - not yet blue/green.
#
#   ./deploy.sh              build + deploy both services
#   ./deploy.sh backend      build + deploy one service
#   ./deploy.sh --list       show available rollback points
#   ./deploy.sh --rollback backend 20260725_191204
#   ./deploy.sh --status     show which backend color is live, on which port
set -euo pipefail

cd "$(dirname "$0")"
KEEP=5

list_tags() {
  for svc in backend frontend; do
    echo "edusmart-$svc:"
    docker images "edusmart-$svc" --format '  {{.Tag}}  ({{.CreatedSince}}, {{.Size}})' \
      | grep -v '  latest' || echo "  (none yet)"
  done
}

# Block until a container reports healthy. `docker compose up -d` returns as
# soon as the container is *started*, which is well before the app can serve -
# without this a rollback looks like it silently did nothing.
wait_healthy() {
  local name="$1" s=""
  for _ in $(seq 1 45); do
    s=$(docker inspect "$name" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || echo missing)
    [ "$s" = "healthy" ] || [ "$s" = "none" ] && break
    sleep 2
  done
  echo "$s"
}

# --- Backend blue/green helpers -------------------------------------------

backend_color_port() { [ "$1" = "blue" ] && echo 8000 || echo 8001; }
backend_other_color() { [ "$1" = "blue" ] && echo green || echo blue; }

# Which color is currently running, if any. Empty string means neither is up
# (a fresh box's first-ever deploy) - not an error case.
backend_active_color() {
  if [ "$(docker inspect -f '{{.State.Running}}' edusmart-backend-blue 2>/dev/null)" = "true" ]; then
    echo "blue"
  elif [ "$(docker inspect -f '{{.State.Running}}' edusmart-backend-green 2>/dev/null)" = "true" ]; then
    echo "green"
  else
    echo ""
  fi
}

# Wait (bounded) for a color's in-flight generation jobs to finish before
# stopping it, so a routine deploy doesn't kill someone's story mid-generation.
# On timeout, stops anyway - job_queue.py's CancelledError handler marks any
# still-running job as failed rather than leaving it stuck, and main.py's
# startup reconciler refunds the credit on next boot. A bounded wait beats an
# unbounded one: a single wedged generation must not block every deploy forever.
backend_drain_color() {
  local color="$1" port; port=$(backend_color_port "$color")
  local waited=0 max=180
  while [ "$waited" -lt "$max" ]; do
    local running
    running=$(curl -s --max-time 5 "http://127.0.0.1:$port/api/health" 2>/dev/null \
      | python3 -c "import json,sys; print(json.load(sys.stdin).get('queue',{}).get('running',0))" 2>/dev/null || echo 0)
    [ "$running" = "0" ] && return 0
    echo "  draining edusmart-backend-$color: $running job(s) still running (${waited}s/${max}s)..."
    sleep 5
    waited=$((waited + 5))
  done
  echo "  !! drain timeout on edusmart-backend-$color after ${max}s - stopping anyway"
  echo "     (any in-flight job is marked failed + refunded on next backend startup, see job_queue.py)"
}

# Roll out whatever image is currently tagged edusmart-backend:latest to the
# inactive color, health-check it standalone, then retire the old one. Shared
# by both the normal build-and-deploy path and --rollback.
backend_roll_out() {
  local active target
  active=$(backend_active_color)
  if [ -z "$active" ]; then
    target="blue"
    echo "==> no backend color currently running (fresh deploy) - starting blue"
  else
    target=$(backend_other_color "$active")
    echo "==> backend-$active is live; deploying to backend-$target"
  fi

  docker compose up -d --no-deps "backend-$target" || true

  # docker compose up can return 0 even when the container failed to actually
  # start (e.g. a host port bind conflict) - it only reports the daemon error
  # to stderr. wait_healthy's "none" branch was written for containers with
  # no HEALTHCHECK at all; it cannot tell that apart from "never started",
  # so check the running state explicitly first rather than trust health status.
  if [ "$(docker inspect -f '{{.State.Running}}' "edusmart-backend-$target" 2>/dev/null)" != "true" ]; then
    echo "  !! backend-$target never started. Check for a port conflict or build error:"
    echo "     docker logs edusmart-backend-$target"
    if [ -n "$active" ]; then
      echo "     backend-$active was never touched - the app is still fully up, no outage."
    fi
    return 1
  fi

  local s; s=$(wait_healthy "edusmart-backend-$target")
  echo "  edusmart-backend-$target: $s"
  if [ "$s" = "unhealthy" ]; then
    echo "  !! backend-$target failed its health check."
    if [ -n "$active" ]; then
      echo "     backend-$active was never touched - the app is still fully up, no outage."
    fi
    echo "     Investigate with: docker logs edusmart-backend-$target"
    return 1
  fi

  if [ -n "$active" ]; then
    echo "==> backend-$target healthy; draining backend-$active before stopping it"
    backend_drain_color "$active"
    docker compose stop "backend-$active"
    echo "  edusmart-backend-$active stopped"
  fi
  return 0
}

deploy_backend() {
  echo "==> building backend"
  # Both colors share one image (image: edusmart-backend:latest in
  # docker-compose.yml) - building via backend-blue's build context is
  # identical to building via backend-green's, so there is exactly one build
  # regardless of which color ends up as the deploy target.
  docker compose build --build-arg "BUILD_TAG=$STAMP" backend-blue
  docker tag "edusmart-backend:latest" "edusmart-backend:$STAMP"

  backend_roll_out || {
    echo "  !! Roll back the image tag with:"
    echo "     ./deploy.sh --rollback backend $(docker images edusmart-backend --format '{{.Tag}}' | grep -v latest | grep -v "$STAMP" | head -1)"
    exit 1
  }

  docker images "edusmart-backend" --format '{{.Tag}} {{.CreatedAt}}' \
    | grep -v '^latest ' | sort -k2 -r | tail -n +$((KEEP + 1)) | awk '{print $1}' \
    | while read -r old; do docker rmi "edusmart-backend:$old" >/dev/null 2>&1 || true; done
}

rollback_backend() {
  local tag="$1"
  docker image inspect "edusmart-backend:$tag" >/dev/null 2>&1 \
    || { echo "No such image: edusmart-backend:$tag"; exit 1; }
  docker tag "edusmart-backend:$tag" "edusmart-backend:latest"
  backend_roll_out || exit 1
  echo "Rolled backend back to $tag."
}

# --- Frontend blue/green ---------------------------------------------------
# Same pattern as backend, minus the drain step: frontend is stateless (no
# in-flight jobs, no shared volume) - a request cut mid-response by the old
# container stopping just gets retried against the live one via aaPanel's
# host-nginx upstream (edusmart-frontend-upstream.conf, blue/green pool with
# passive failover), same as any other connection blip.

frontend_color_port() { [ "$1" = "blue" ] && echo 3004 || echo 3009; }
frontend_other_color() { [ "$1" = "blue" ] && echo green || echo blue; }

frontend_active_color() {
  if [ "$(docker inspect -f '{{.State.Running}}' edusmart-frontend-blue 2>/dev/null)" = "true" ]; then
    echo "blue"
  elif [ "$(docker inspect -f '{{.State.Running}}' edusmart-frontend-green 2>/dev/null)" = "true" ]; then
    echo "green"
  else
    echo ""
  fi
}

# Roll out whatever image is currently tagged edusmart-frontend:latest to the
# inactive color, health-check it standalone, then retire the old one. Shared
# by both the normal build-and-deploy path and --rollback.
frontend_roll_out() {
  local active target
  active=$(frontend_active_color)
  if [ -z "$active" ]; then
    target="blue"
    echo "==> no frontend color currently running (fresh deploy) - starting blue"
  else
    target=$(frontend_other_color "$active")
    echo "==> frontend-$active is live; deploying to frontend-$target"
  fi

  docker compose up -d --no-deps "frontend-$target" || true

  if [ "$(docker inspect -f '{{.State.Running}}' "edusmart-frontend-$target" 2>/dev/null)" != "true" ]; then
    echo "  !! frontend-$target never started. Check for a port conflict or build error:"
    echo "     docker logs edusmart-frontend-$target"
    if [ -n "$active" ]; then
      echo "     frontend-$active was never touched - the app is still fully up, no outage."
    fi
    return 1
  fi

  local s; s=$(wait_healthy "edusmart-frontend-$target")
  echo "  edusmart-frontend-$target: $s"
  if [ "$s" = "unhealthy" ]; then
    echo "  !! frontend-$target failed its health check."
    if [ -n "$active" ]; then
      echo "     frontend-$active was never touched - the app is still fully up, no outage."
    fi
    echo "     Investigate with: docker logs edusmart-frontend-$target"
    return 1
  fi

  if [ -n "$active" ]; then
    docker compose stop "frontend-$active"
    echo "  edusmart-frontend-$active stopped"
  fi
  return 0
}

deploy_frontend() {
  echo "==> building frontend"
  # Both colors share one image (image: edusmart-frontend:latest in
  # docker-compose.yml) - exactly one build regardless of which color ends
  # up as the deploy target.
  docker compose build --build-arg "BUILD_TAG=$STAMP" frontend-blue
  docker tag "edusmart-frontend:latest" "edusmart-frontend:$STAMP"

  frontend_roll_out || {
    echo "  !! Roll back the image tag with:"
    echo "     ./deploy.sh --rollback frontend $(docker images edusmart-frontend --format '{{.Tag}}' | grep -v latest | grep -v "$STAMP" | head -1)"
    exit 1
  }

  docker images "edusmart-frontend" --format '{{.Tag}} {{.CreatedAt}}' \
    | grep -v '^latest ' | sort -k2 -r | tail -n +$((KEEP + 1)) | awk '{print $1}' \
    | while read -r old; do docker rmi "edusmart-frontend:$old" >/dev/null 2>&1 || true; done
}

rollback_frontend() {
  local tag="$1"
  docker image inspect "edusmart-frontend:$tag" >/dev/null 2>&1 \
    || { echo "No such image: edusmart-frontend:$tag"; exit 1; }
  docker tag "edusmart-frontend:$tag" "edusmart-frontend:latest"
  frontend_roll_out || exit 1
  echo "Rolled frontend back to $tag."
}

status() {
  local active; active=$(backend_active_color)
  if [ -z "$active" ]; then
    echo "backend: NEITHER color is running"
  else
    local port; port=$(backend_color_port "$active")
    echo "backend: $active is live (edusmart-backend-$active, host port $port)"
  fi

  local factive; factive=$(frontend_active_color)
  if [ -z "$factive" ]; then
    echo "frontend: NEITHER color is running"
  else
    local fport; fport=$(frontend_color_port "$factive")
    echo "frontend: $factive is live (edusmart-frontend-$factive, host port $fport)"
  fi
}

case "${1:-}" in
  --list)     list_tags; exit 0 ;;
  --status)   status; exit 0 ;;
  --rollback)
    case "${2:?service}" in
      backend)  rollback_backend "${3:?tag}" ;;
      frontend) rollback_frontend "${3:?tag}" ;;
      *) echo "Unknown service: $2"; exit 1 ;;
    esac
    exit 0
    ;;
esac

SERVICES=("${@:-backend frontend}")
read -ra SERVICES <<< "${SERVICES[*]}"
STAMP=$(date +%Y%m%d_%H%M%S)

# The backend runs as 1001:1001 (see docker-compose.yml) and writes to every
# bind mount below. If any of these is missing, Docker creates it as root; if
# it gets recreated by a tool running as www, it lands 0775 www:www. Either way
# the container cannot mkdir inside it and EVERY upload 500s with EACCES -
# which is exactly what happened to generated_stories on 2026-07-26. The app
# has no way to detect this at startup, so assert it at deploy time instead.
for d in outputs uploads saved_stories generated_stories; do
  [ -d "backend/$d" ] || sudo install -d -o 1001 -g 1001 -m 775 "backend/$d"
  owner=$(stat -c '%u' "backend/$d")
  if [ "$owner" != "1001" ]; then
    echo "==> fixing ownership of backend/$d (was uid $owner, container needs 1001)"
    sudo chown -R 1001:1001 "backend/$d"
  fi
done

for svc in "${SERVICES[@]}"; do
  case "$svc" in
    backend)  deploy_backend ;;
    frontend) deploy_frontend ;;
    *) echo "Unknown service: $svc"; exit 1 ;;
  esac
done

echo "==> deployed $STAMP"
status
curl -s https://edusmart.ign3el.com/api/health; echo
