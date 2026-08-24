#!/usr/bin/env bash
# Build, push and deploy the agent to AWS Lambda (STAGE 4).
#
# The ECR repository has to exist before an image can be pushed, and the Lambda
# needs the image before it can be created — so Terraform runs twice, with the
# push in between. That ordering is the whole reason this is a script.
set -euo pipefail

PROJECT="${1:-aws-monitoring-agent}"
REGION="${2:-us-east-1}"
TF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../deploy/terraform" && pwd)"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="$(date -u +%Y%m%d%H%M%S)"

for binary in terraform docker aws; do
  command -v "${binary}" >/dev/null || { echo "${binary} is required but not on PATH" >&2; exit 1; }
done
aws sts get-caller-identity --region "${REGION}" >/dev/null || {
  echo "AWS credentials are not working — run 'aws configure'" >&2
  exit 1
}

TF_ARGS=(-var "aws_region=${REGION}" -var "project_name=${PROJECT}")

echo "==> [1/5] terraform init"
terraform -chdir="${TF_DIR}" init -input=false

echo "==> [2/5] creating the ECR repository and the secret"
terraform -chdir="${TF_DIR}" apply -input=false -auto-approve \
  "${TF_ARGS[@]}" \
  -target=aws_ecr_repository.agent \
  -target=aws_secretsmanager_secret.anthropic

REPO_URL="$(terraform -chdir="${TF_DIR}" output -raw ecr_repository_url)"
REGISTRY="${REPO_URL%%/*}"

echo "==> [3/5] building the image"
docker build -t "${REPO_URL}:${TAG}" -t "${REPO_URL}:latest" "${ROOT}"

echo "==> [4/5] pushing to ${REPO_URL}"
aws ecr get-login-password --region "${REGION}" | docker login --username AWS --password-stdin "${REGISTRY}"
docker push "${REPO_URL}:${TAG}"
docker push "${REPO_URL}:latest"

echo "==> [5/5] deploying"
terraform -chdir="${TF_DIR}" apply -input=false -auto-approve "${TF_ARGS[@]}" -var "image_tag=${TAG}"

SECRET_ARN="$(terraform -chdir="${TF_DIR}" output -raw anthropic_secret_arn)"
echo ""
echo "======================================================================"
terraform -chdir="${TF_DIR}" output
echo "======================================================================"
echo ""
echo " If you have not stored the API key yet:"
echo "   aws secretsmanager put-secret-value --region ${REGION} \\"
echo "     --secret-id ${SECRET_ARN} --secret-string 'sk-ant-...'"
echo ""
echo " Then check it works:  make invoke"
echo ""
