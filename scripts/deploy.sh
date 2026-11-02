#!/usr/bin/env bash
# The deploy gate: tests run against the REAL stack first. If anything fails, this script stops and nothing is deployed.
set -euo pipefail

cleanup() { docker compose --profile test down -v >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "==> Building and running the regression tests against the real stack..."
if ! docker compose --profile test run --rm --build tests; then
  echo "BLOCKED: tests failed. Nothing was deployed."
  exit 1
fi

echo "==> Tests passed. Safe to deploy."
# Put your real deploy command here, for example:
#   docker push your-registry/kitchen-api:latest
