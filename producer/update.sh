#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Mikey Sklar
# SPDX-License-Identifier: GPL-3.0-or-later
# Manual end-to-end run: GitHub -> scored rows -> panel text -> Adafruit IO.
#
#   ./update.sh hathach/tinyusb
#   ./update.sh adafruit/circuitpython --limit 60
#   DRY_RUN=1 ./update.sh hathach/tinyusb      # render only, nothing sent
set -euo pipefail
cd "$(dirname "$0")"

REPO="${1:?usage: ./update.sh owner/repo [--limit N] [--core-path P]...}"
shift

LIMIT_ARGS=(); SCORE_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --limit) LIMIT_ARGS+=(--limit "$2"); shift 2 ;;
    --since-days) LIMIT_ARGS+=(--since-days "$2"); shift 2 ;;
    --core-path|--stale-days) SCORE_ARGS+=("$1" "$2"); shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

WORK="${WORK:-./.work}"
mkdir -p "$WORK"

./collect.py "$REPO" ${LIMIT_ARGS[@]+"${LIMIT_ARGS[@]}"} -o "$WORK/raw.json"
./score.py "$WORK/raw.json" ${SCORE_ARGS[@]+"${SCORE_ARGS[@]}"} -o "$WORK/rows.json"
./render.py "$WORK/rows.json" -o "$WORK/payload.txt"

if [ -n "${DRY_RUN:-}" ]; then
  ./push.py "$WORK/payload.txt" --dry-run
else
  ./push.py "$WORK/payload.txt"
fi
