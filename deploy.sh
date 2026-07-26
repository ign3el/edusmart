#!/usr/bin/env bash
# EduSmart deploy — builds a TAGGED image so rollback actually exists.
#
# The backend no longer bind-mounts its source, so the running container is
# exactly the image built here. That is only useful if you can go back to the
# previous one, which is what the timestamp tags are for.
#
#   ./deploy.sh              build + deploy both services
#   ./deploy.sh backend      build + deploy one service
#   ./deploy.sh --list       show available rollback points
#   ./deploy.sh --rollback backend 20260725_191204
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

# Block until the container reports healthy. `docker compose up -d` returns as
# soon as the container is *started*, which is well before the app can serve -
# without this a rollback looks like it silently did nothing.
wait_healthy() {
  local name="edusmart-$1" s=""
  for _ in $(seq 1 45); do
    s=$(docker inspect "$name" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || echo missing)
    [ "$s" = "healthy" ] || [ "$s" = "none" ] && break
    sleep 2
  done
  echo "$s"
}

rollback() {
  local svc="$1" tag="$2"
  docker image inspect "edusmart-$svc:$tag" >/dev/null 2>&1 \
    || { echo "No such image: edusmart-$svc:$tag"; exit 1; }
  docker tag "edusmart-$svc:$tag" "edusmart-$svc:latest"
  docker compose up -d --no-deps "$svc"
  local s; s=$(wait_healthy "$svc")
  echo "Rolled $svc back to $tag (health: $s)."
  [ "$s" = "unhealthy" ] && exit 1
  return 0
}

case "${1:-}" in
  --list)     list_tags; exit 0 ;;
  --rollback) rollback "${2:?service}" "${3:?tag}"; exit 0 ;;
esac

SERVICES=("${@:-backend frontend}")
read -ra SERVICES <<< "${SERVICES[*]}"
STAMP=$(date +%Y%m%d_%H%M%S)

for svc in "${SERVICES[@]}"; do
  echo "==> building $svc"
  # --no-deps so building one service never silently recreates the other
  # (frontend depends_on backend, which used to wipe the backend's logs).
  docker compose build --build-arg "BUILD_TAG=$STAMP" "$svc"
  docker tag "edusmart-$svc:latest" "edusmart-$svc:$STAMP"
  docker compose up -d --no-deps "$svc"

  # Keep the newest $KEEP timestamped tags; the rest are just disk.
  docker images "edusmart-$svc" --format '{{.Tag}} {{.CreatedAt}}' \
    | grep -v '^latest ' | sort -k2 -r | tail -n +$((KEEP + 1)) | awk '{print $1}' \
    | while read -r old; do docker rmi "edusmart-$svc:$old" >/dev/null 2>&1 || true; done
done

echo "==> waiting for health"
for svc in "${SERVICES[@]}"; do
  s=$(wait_healthy "$svc")
  echo "  edusmart-$svc: $s"
  if [ "$s" = "unhealthy" ]; then
    echo "  !! $svc is unhealthy. Roll back with:"
    echo "     ./deploy.sh --rollback $svc $(docker images "edusmart-$svc" --format '{{.Tag}}' | grep -v latest | grep -v "$STAMP" | head -1)"
    exit 1
  fi
done

echo "==> deployed $STAMP"
curl -s https://edusmart.ign3el.com/api/health; echo
