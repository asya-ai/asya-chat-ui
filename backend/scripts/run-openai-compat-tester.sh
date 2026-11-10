#!/usr/bin/env bash
# Run beranekio/openai-compatibility-tester against a live OpenAI-compatible API.
# Usage:
#   export OPENAI_BASE_URL=https://chat.example.com/api/v1
#   export OPENAI_API_KEY=ak_...
#   export OPENAI_MODEL=your-model
#   ./backend/scripts/run-openai-compat-tester.sh default
#
# Presets: default | extended | full | or comma-separated suite names.

set -euo pipefail

PRESET="${1:-default}"

if [[ -z "${OPENAI_BASE_URL:-}" || -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_BASE_URL and OPENAI_API_KEY are required." >&2
  exit 2
fi

IMAGE="${OPENAI_COMPAT_TESTER_IMAGE:-ghcr.io/beranekio/openai-compatibility-tester:latest}"

case "${PRESET}" in
  default|extended|full)
    SUITES="${PRESET}"
    ;;
  *)
    SUITES="${PRESET}"
    ;;
esac

exec docker run --rm \
  -e OPENAI_BASE_URL \
  -e OPENAI_API_KEY \
  -e OPENAI_MODEL="${OPENAI_MODEL:-gpt-4o-mini}" \
  -e OPENAI_VISION_MODEL="${OPENAI_VISION_MODEL:-$OPENAI_MODEL}" \
  -e OPENAI_EMBEDDING_MODEL="${OPENAI_EMBEDDING_MODEL:-}" \
  -e OPENAI_COMPLETION_MODEL="${OPENAI_COMPLETION_MODEL:-$OPENAI_MODEL}" \
  -e TEST_SUITES="${SUITES}" \
  -e REQUEST_TIMEOUT="${REQUEST_TIMEOUT:-4m}" \
  "${IMAGE}"
