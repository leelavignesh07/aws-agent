#!/usr/bin/env bash
# Invoke the deployed agent and print what it reports (STAGE 4).
set -euo pipefail

PROJECT="${1:-aws-monitoring-agent}"
REGION="${2:-us-east-1}"
PAYLOAD="${PAYLOAD:-{\"action\":\"report\",\"include_security\":true}}"
OUT="$(mktemp)"
trap 'rm -f "${OUT}"' EXIT

echo "==> invoking ${PROJECT} in ${REGION}"
aws lambda invoke \
  --function-name "${PROJECT}" \
  --region "${REGION}" \
  --cli-binary-format raw-in-base64-out \
  --payload "${PAYLOAD}" \
  --no-cli-pager \
  "${OUT}" >/dev/null

if command -v jq >/dev/null 2>&1; then
  jq . "${OUT}"
else
  cat "${OUT}"
  echo ""
fi

echo ""
echo "==> full report text is in CloudWatch:  make deployed-logs"
