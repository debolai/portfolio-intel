#!/usr/bin/env bash
# scripts/env_down.sh — destroy the runtime; foundation (data, images, audit, logs) is untouched
set -euo pipefail
ENV="${1:-dev}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
if [[ "${CONFIRM:-}" != "yes" ]]; then
  read -rp "Destroy the $ENV runtime? Data, images, audit trail and logs are kept. [y/N] " a
  [[ "$a" == "y" ]] || exit 1
fi
cd "$ROOT/infra/terraform/runtime"
terraform init -input=false -reconfigure \
  -backend-config="bucket=pi-tfstate-$ACCOUNT" -backend-config="key=$ENV/runtime.tfstate" >/dev/null
TAG=$(aws ssm get-parameter --name "/pi/$ENV/last_green_image_tag" --query Parameter.Value --output text)
terraform destroy -input=false -auto-approve -parallelism=20 -var env="$ENV" -var image_tag="$TAG"
aws ssm delete-parameter --name "/pi/$ENV/expires_at" 2>/dev/null || true
LEFT=$(aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=pi:layer,Values=runtime Key=pi:env,Values="$ENV" \
  --query 'length(ResourceTagMappingList)' --output text)
echo "✅ $ENV runtime destroyed. Tagged runtime resources still listed: $LEFT (the tagging API can lag a few minutes)."
