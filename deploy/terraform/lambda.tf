resource "aws_cloudwatch_log_group" "agent" {
  name              = "/aws/lambda/${var.project_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "agent" {
  function_name = var.project_name
  description   = "Read-only AWS monitoring agent (status, health, inventory, cost)"
  role          = aws_iam_role.agent.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.agent.repository_url}:${var.image_tag}"
  memory_size   = var.lambda_memory_mb
  timeout       = var.lambda_timeout_seconds
  architectures = ["x86_64"]

  environment {
    variables = {
      AGENT_MODEL           = var.agent_model
      AGENT_APPROVAL_MODE   = var.approval_mode
      AGENT_STATE_DIR       = "/tmp/agent-state"
      AGENT_JSON_LOGS       = "true"
      AGENT_ACTOR           = "lambda:${var.project_name}"
      ANTHROPIC_SECRET_ARN  = local.anthropic_secret_arn
      REPORT_TOPIC_ARN      = join("", aws_sns_topic.reports[*].arn)
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
