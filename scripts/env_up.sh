#!/usr/bin/env bash
# scripts/env_up.sh — create or update the runtime from the last green image
set -euo pipefail
ENV="${1:-dev}"
TTL_HOURS="${TTL_HOURS:-48}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
START=$(date +%s)
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

TAG=$(aws ssm get-parameter --name "/pi/$ENV/last_green_image_tag" --query Parameter.Value --output text)
[[ "$TAG" != "none" ]] || { echo "No green image yet. Merge to main first."; exit 1; }
aws ecr describe-images --repository-name portfolio-intel --image-ids imageTag="$TAG" >/dev/null
echo "▶ Deploying last green image $TAG to $ENV"

cd "$ROOT/infra/terraform/runtime"
terraform init -input=false -reconfigure \
  -backend-config="bucket=pi-tfstate-$ACCOUNT" -backend-config="key=$ENV/runtime.tfstate" >/dev/null
terraform apply -input=false -auto-approve -parallelism=20 -var env="$ENV" -var image_tag="$TAG"
CLUSTER=$(terraform output -raw cluster_name)
URL=$(terraform output -raw base_url)

echo "▶ Waiting for ECS services to stabilise"
aws ecs wait services-stable --cluster "$CLUSTER" --services pi-api pi-mcp

if [[ "${REFRESH:-0}" == "1" ]]; then
  echo "▶ Starting a one-off ingest in the background"
  aws ecs run-task --cli-input-json "$(terraform output -raw ingest_run_task_input)" >/dev/null
fi

PI_MCP_TOKEN=$(aws secretsmanager get-secret-value --secret-id "pi/$ENV/app" \
  --query SecretString --output text | jq -r .PI_MCP_TOKEN)
export PI_MCP_TOKEN
(cd "$ROOT" && uv run python scripts/smoke.py "$URL" "$URL/mcp" --with-llm)

if [[ "${KEEP_EXPIRY:-0}" != "1" ]]; then
  EXPIRES=$(date -u -d "+${TTL_HOURS} hours" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
            || date -u -v+"${TTL_HOURS}"H +%Y-%m-%dT%H:%M:%SZ)          # GNU date, then macOS
  aws ssm put-parameter --name "/pi/$ENV/expires_at" --value "$EXPIRES" --type String --overwrite >/dev/null
fi

echo "✅ $ENV is up in $(( ($(date +%s) - START) / 60 )) min: $URL"
echo "   Auto-teardown: $(aws ssm get-parameter --name "/pi/$ENV/expires_at" --query Parameter.Value --output text 2>/dev/null || echo none)"
echo "   Claude Desktop: npx mcp-remote $URL/mcp --header \"Authorization: Bearer \$PI_MCP_TOKEN\""
