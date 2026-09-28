#!/usr/bin/env bash
# scripts/env_status.sh — prints "up"/"down" with --quiet, otherwise a one-line summary
set -euo pipefail
ENV="${1:-dev}"
ACTIVE=$(aws ecs describe-clusters --clusters "pi-$ENV" \
  --query 'length(clusters[?status==`ACTIVE`])' --output text 2>/dev/null || echo 0)
STATE=$([[ "$ACTIVE" == "1" ]] && echo up || echo down)
[[ "${2:-}" == "--quiet" ]] && { echo "$STATE"; exit 0; }
TAG=$(aws ssm get-parameter --name "/pi/$ENV/last_green_image_tag" --query Parameter.Value --output text)
EXP=$(aws ssm get-parameter --name "/pi/$ENV/expires_at" --query Parameter.Value --output text 2>/dev/null || echo "-")
echo "runtime: $STATE | last green image: $TAG | auto-teardown: $EXP"
