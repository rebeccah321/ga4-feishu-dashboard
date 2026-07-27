#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

MOCK=0
FETCH_ONLY=0
PUSH_BASE=0
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --mock) MOCK=1 ;;
    --fetch-only) FETCH_ONLY=1 ;;
    --push-base) PUSH_BASE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

FETCH_ARGS=(fetch)
if [[ "$MOCK" == "1" ]]; then
  FETCH_ARGS+=(--mock)
fi

python3 scripts/ga4_pipeline.py "${FETCH_ARGS[@]}"
python3 scripts/ga4_pipeline.py render-dashboard

if [[ "$FETCH_ONLY" == "1" ]]; then
  exit 0
fi

if [[ "$PUSH_BASE" == "1" ]]; then
  PUSH_ARGS=(push-base)
  if [[ "$DRY_RUN" == "1" ]]; then
    PUSH_ARGS+=(--dry-run)
  fi
  python3 scripts/ga4_pipeline.py "${PUSH_ARGS[@]}"
fi
