#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
STACK_NAME="${CIVITAS_STACK_NAME:-civitasx-prod}"
AWS_REGION_NAME="${AWS_REGION:-ap-south-1}"
STAGE_NAME="${CIVITAS_STAGE_NAME:-prod}"
FRONTEND_ORIGIN="${CIVITAS_FRONTEND_ORIGIN:-http://localhost:5173}"
BEDROCK_MODEL="${CIVITAS_BEDROCK_MODEL:-apac.amazon.nova-lite-v1:0}"
MONTHLY_BUDGET_USD="${CIVITAS_MONTHLY_BUDGET_USD:-20}"
BROWSER_CONCURRENCY="${CIVITAS_BROWSER_CONCURRENCY:-2}"
ATTACHMENT_MAX_BYTES="${CIVITAS_ATTACHMENT_MAX_BYTES:-5242880}"
ALERT_EMAIL="${CIVITAS_ALERT_EMAIL:-}"

command -v aws >/dev/null || { echo "aws CLI is required" >&2; exit 1; }
command -v sam >/dev/null || { echo "SAM CLI is required" >&2; exit 1; }
command -v uv >/dev/null || { echo "uv is required to build the Lambda package" >&2; exit 1; }
command -v rsync >/dev/null || { echo "rsync is required to stage the Lambda package" >&2; exit 1; }

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
COGNITO_DOMAIN_PREFIX="${CIVITAS_COGNITO_DOMAIN_PREFIX:-civitasx-${STAGE_NAME}-${ACCOUNT_ID}}"
MCP_ALLOWED_HOSTS='localhost:*,127.0.0.1:*'

if [[ -z "${CIVITAS_COGNITO_DOMAIN_PREFIX:-}" ]]; then
  EXISTING_COGNITO_DOMAIN_PREFIX="$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION_NAME" \
    --query "Stacks[0].Parameters[?ParameterKey=='CognitoDomainPrefix'].ParameterValue" \
    --output text 2>/dev/null || true)"
  if [[ -z "${CIVITAS_COGNITO_DOMAIN_PREFIX:-}" \
    && -n "$EXISTING_COGNITO_DOMAIN_PREFIX" \
    && "$EXISTING_COGNITO_DOMAIN_PREFIX" != "None" ]]; then
    # Preserve the registered Cognito domain on updates; changing it forces
    # replacement and can fail or invalidate the hosted sign-in configuration.
    COGNITO_DOMAIN_PREFIX="$EXISTING_COGNITO_DOMAIN_PREFIX"
  fi
fi

if [[ -z "${CIVITAS_FRONTEND_ORIGIN:-}" ]]; then
  EXISTING_WEB_URL="$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='WebUrl'].OutputValue" \
    --output text 2>/dev/null || true)"
  EXISTING_API_URL="$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
    --output text 2>/dev/null || true)"
  if [[ -n "$EXISTING_WEB_URL" && "$EXISTING_WEB_URL" != "None" ]]; then
    FRONTEND_ORIGIN="$EXISTING_WEB_URL"
  fi
  if [[ -n "$EXISTING_API_URL" && "$EXISTING_API_URL" != "None" ]]; then
    MCP_ALLOWED_HOSTS="${EXISTING_API_URL#https://}"
    MCP_ALLOWED_HOSTS="${MCP_ALLOWED_HOSTS%/}"
  fi
fi

BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/civitasx-deploy.XXXXXX")"
trap 'rm -rf "$BUILD_ROOT"' EXIT
PACKAGE_ROOT="$BUILD_ROOT/lambda"
mkdir -p "$PACKAGE_ROOT"

echo "Building Linux ARM64 Lambda dependencies..."
uv pip install \
  --target "$PACKAGE_ROOT" \
  --python-version 3.12 \
  --python-platform aarch64-manylinux2014 \
  --only-binary=:all: \
  -r "$ROOT_DIR/services/api/requirements.txt"

rsync -a "$ROOT_DIR/services/api/src" "$PACKAGE_ROOT/"
rsync -a "$ROOT_DIR/services/api/data" "$PACKAGE_ROOT/"
rsync -a "$ROOT_DIR/services/api/policies" "$PACKAGE_ROOT/"
cp "$ROOT_DIR/services/api/run.sh" "$PACKAGE_ROOT/run.sh"
chmod 755 "$PACKAGE_ROOT/run.sh"

TEMPLATE_PATH="$BUILD_ROOT/template.yaml"
PACKAGED_TEMPLATE_PATH="$BUILD_ROOT/packaged-template.yaml"
cp "$ROOT_DIR/infra/template.yaml" "$TEMPLATE_PATH"
if [[ "$(uname -s)" == "Darwin" ]]; then
  sed -i '' "s|CodeUri: ../services/api/|CodeUri: $PACKAGE_ROOT|" "$TEMPLATE_PATH"
else
  sed -i "s|CodeUri: ../services/api/|CodeUri: $PACKAGE_ROOT|" "$TEMPLATE_PATH"
fi

sam validate --template-file "$TEMPLATE_PATH" --lint
sam package \
  --template-file "$TEMPLATE_PATH" \
  --resolve-s3 \
  --output-template-file "$PACKAGED_TEMPLATE_PATH"

deploy_stack() {
  local frontend_origin="$1"
  local mcp_hosts="$2"
  local -a params=(
    "StageName=$STAGE_NAME"
    "FrontendOrigin=$frontend_origin"
    "CognitoDomainPrefix=$COGNITO_DOMAIN_PREFIX"
    "MonthlyBudgetUsd=$MONTHLY_BUDGET_USD"
    "BrowserConcurrency=$BROWSER_CONCURRENCY"
    "McpAllowedHosts=$mcp_hosts"
    "BedrockModel=$BEDROCK_MODEL"
    "AttachmentMaxBytes=$ATTACHMENT_MAX_BYTES"
  )
  if [[ -n "$ALERT_EMAIL" ]]; then
    params+=("AlertEmail=$ALERT_EMAIL")
  fi
  sam deploy \
    --template-file "$PACKAGED_TEMPLATE_PATH" \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION_NAME" \
    --capabilities CAPABILITY_NAMED_IAM \
    --no-confirm-changeset \
    --no-fail-on-empty-changeset \
    --parameter-overrides "${params[@]}"
}

deploy_stack "$FRONTEND_ORIGIN" "$MCP_ALLOWED_HOSTS"

stack_output() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
    --output text
}

API_URL="$(stack_output ApiUrl)"
WEB_URL="$(stack_output WebUrl)"
WEB_BUCKET="$(stack_output WebBucket)"
DISTRIBUTION_ID="$(stack_output WebDistributionId)"
API_HOST="${API_URL#https://}"
API_HOST="${API_HOST%/}"

if [[ "$FRONTEND_ORIGIN" != "$WEB_URL" || "$MCP_ALLOWED_HOSTS" != "$API_HOST" ]]; then
  deploy_stack "$WEB_URL" "$API_HOST"
fi

API_URL="$(stack_output ApiUrl)"
WEB_URL="$(stack_output WebUrl)"
WEB_BUCKET="$(stack_output WebBucket)"
DISTRIBUTION_ID="$(stack_output WebDistributionId)"

echo "Building and publishing frontend..."
(cd "$ROOT_DIR/apps/web" && VITE_API_URL="$API_URL" npm run build)
aws s3 sync "$ROOT_DIR/apps/web/dist" "s3://$WEB_BUCKET" \
  --region "$AWS_REGION_NAME" \
  --delete \
  --exclude index.html \
  --cache-control 'public,max-age=31536000,immutable'
aws s3 cp "$ROOT_DIR/apps/web/dist/index.html" "s3://$WEB_BUCKET/index.html" \
  --region "$AWS_REGION_NAME" \
  --cache-control 'no-cache,no-store,must-revalidate' \
  --content-type text/html
aws cloudfront create-invalidation \
  --distribution-id "$DISTRIBUTION_ID" \
  --paths '/*' \
  --query '{Id:Invalidation.Id,Status:Invalidation.Status}' \
  --output json

curl -fsS --max-time 60 "${API_URL%/}/api/health"
printf '\nFrontend: %s\nAPI: %s\n' "$WEB_URL" "$API_URL"
