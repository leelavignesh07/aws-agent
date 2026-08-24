locals {
  # Built by scripts/build_lambda.sh (bash + python, no Docker). `make deploy`
  # passes an absolute path; the default is where the script writes it.
  lambda_package = var.lambda_package_path != "" ? var.lambda_package_path : "${path.module}/../../dist/agent-lambda.zip"
}

resource "aws_cloudwatch_log_group" "agent" {
  name              = "/aws/lambda/${var.project_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "agent" {
  function_name = var.project_name
  description   = "Read-only AWS monitoring agent (status, health, inventory, cost)"
  role          = aws_iam_role.agent.arn

  package_type     = "Zip"
  runtime          = var.lambda_runtime
  handler          = "agent.lambda_handler.handler"
  filename         = local.lambda_package
  source_code_hash = filebase64sha256(local.lambda_package)

  memory_size   = var.lambda_memory_mb
  timeout       = var.lambda_timeout_seconds
  architectures = [var.lambda_architecture]

  environment {
    variables = {
      AGENT_MODEL          = var.agent_model
      AGENT_APPROVAL_MODE  = var.approval_mode
      AGENT_STATE_DIR      = "/tmp/agent-state"
      AGENT_KNOWLEDGE_DIR  = "/var/task/knowledge"
      AGENT_JSON_LOGS      = "true"
      AGENT_ACTOR          = "lambda:${var.project_name}"
      ANTHROPIC_SECRET_ARN = local.anthropic_secret_arn
      REPORT_TOPIC_ARN     = join("", aws_sns_topic.reports[*].arn)

      # The package carries its own AWS CLI: /var/task/bin is not on PATH, so
      # the agent is told where to find it explicitly.
      AGENT_AWS_BIN = "/var/task/bin/aws"
      # The CLI wants a writable home; only /tmp is writable in Lambda.
      HOME = "/tmp"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.agent,
    aws_iam_role_policy_attachment.readonly,
    aws_iam_role_policy_attachment.logs,
  ]
}

resource "aws_lambda_function_url" "agent" {
  count              = var.enable_function_url ? 1 : 0
  function_name      = aws_lambda_function.agent.function_name
  authorization_type = "AWS_IAM" # SigV4 required — never NONE for an endpoint that reads your account
}
