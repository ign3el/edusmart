#!/usr/bin/env bash
# Run the backend test suite.
#
# Tests execute inside a THROWAWAY container built from the current backend
# image, not on the host and not in the running production container:
#   - the host has none of the production dependencies installed
#   - tests/ is excluded from the image on purpose (pytest and test fixtures
#     have no business in a production artifact), so it is bind-mounted in here
#   - the running container is serving live traffic and must not have pytest,
#     test data, or a test run's memory pressure introduced into it
#
# The suite talks to the real MySQL because that is what the code under test
# talks to. Every DB-touching test creates and deletes its own throwaway user
# (see tests/conftest.py) and never asserts on a real account.
#
# Usage:
#   ./run-tests.sh              # whole suite
#   ./run-tests.sh -k credits   # pytest args pass straight through
set -euo pipefail

cd "$(dirname "$0")"

IMAGE="${TEST_IMAGE:-edusmart-backend:latest}"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Image $IMAGE not found. Run ./deploy.sh backend first." >&2
  exit 1
fi

# --network: join the same network as the running backend so MySQL resolves
# exactly as it does in production. --env-file: the same configuration the real
# container receives.
# Backend deploys blue/green now (see PROJECT.md - "Zero-downtime deploys") -
# there is no longer a single "edusmart-backend" container, so try both color
# names and use whichever is actually running.
# The backend is attached to more than one network (its own compose network and
# the shared ai-services one), so emit newline-separated names and take the
# first. Without the separator the names concatenate into one invalid string.
NETWORK=""
for BACKEND_CONTAINER in edusmart-backend-blue edusmart-backend-green; do
  NETWORK="$(docker inspect "$BACKEND_CONTAINER" \
    --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}' 2>/dev/null \
    | grep -v '^$' | head -1)" || true
  [ -n "$NETWORK" ] && break
done
NETWORK="${NETWORK:-bridge}"

echo "==> running tests in a throwaway container (image=$IMAGE, network=$NETWORK)"

docker run --rm \
  --network "$NETWORK" \
  --env-file .env \
  -v "$PWD/backend/tests:/app/tests:ro" \
  -w /app \
  --entrypoint sh \
  "$IMAGE" -c "
    pip install --quiet --disable-pip-version-check pytest pymupdf 2>/dev/null
    python -m pytest tests/ -v --tb=short -p no:cacheprovider $*
  "
