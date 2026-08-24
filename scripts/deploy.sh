#!/usr/bin/env bash
# Build and deploy the agent to AWS Lambda (STAGE 4). Bash, python, terraform —
# no Docker anywhere.
#
# The package is a plain zip built by scripts/build_lambda.sh, so unlike a
# container deploy there is no registry to populate first: one terraform apply
# creates everything and uploads the code in the same pass.
set -euo pipefail

PROJECT="${1:-aws-monitoring-agent}"
REGION="${2:-us-east-1}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="${ROOT}/deploy/terraform"
PACKAGE="${ROOT}/dist/agent-lambda.zip"

export AWS_PAGER=""

for binary in terraform aws; do
  command -v "${binary}" >/dev/null || { echo "${binary} is required but not on PATH" >&2; exit 1; }
done
aws sts get-caller-identity --region "${REGION}" >/dev/null || {
  echo "AWS credentials are not working — run 'aws configure'" >&2
  exit 1
}

echo "==> [1/3] building the deployment package"
bash "${ROOT}/scripts/build_lambda.sh"

TF_ARGS=(
  -var "aws_region=${REGION}"
  -var "project_name=${PROJECT}"
  -var "lambda_package_path=${PACKAGE}"
)

echo "==> [2/3] terraform init"
terraform -chdir="${TF_DIR}" init -input=false

echo "==> [3/3] deploying"
terraform -chdir="${TF_DIR}" apply -input=false -auto-approve "${TF_ARGS[@]}"

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
