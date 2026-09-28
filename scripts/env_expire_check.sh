#!/usr/bin/env bash
# scripts/env_expire_check.sh — called hourly by GitHub Actions; tears down expired runtimes
set -euo pipefail
ENV="${1:-dev}"
DIR="$(dirname "$0")"
[[ "$("$DIR/env_status.sh" "$ENV" --quiet)" == "up" ]] || { echo "runtime down"; exit 0; }
EXP=$(aws ssm get-parameter --name "/pi/$ENV/expires_at" --query Parameter.Value --output text 2>/dev/null || echo "")
to_epoch() { date -u -d "$1" +%s 2>/dev/null || date -u -j -f %Y-%m-%dT%H:%M:%SZ "$1" +%s; }
if [[ -z "$EXP" || "$(date -u +%s)" -ge "$(to_epoch "$EXP")" ]]; then
  echo "Expired (${EXP:-no expiry set}); tearing down"   # no expiry = expired: the safe default
  CONFIRM=yes "$DIR/env_down.sh" "$ENV"
else
  echo "Runtime up until $EXP"
fi
