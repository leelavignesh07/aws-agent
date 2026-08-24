# The agent's execution role. Read-only by construction: the policy document in
# deploy/iam/agent-readonly-policy.json allows Describe/List/Get actions only,
# and explicitly denies every call that would vend credentials or object data.

resource "aws_iam_role" "agent" {
  name               = "${var.project_name}-role"
  description        = "Read-only role for the AWS monitoring agent"
  assume_role_policy = file("${path.module}/../iam/trust-policy-lambda.json")
}

resource "aws_iam_policy" "readonly" {
  name        = "${var.project_name}-readonly"
  description = "Describe/List/Get only, with an explicit deny on credential-vending actions"
  policy      = file("${path.module}/../iam/agent-readonly-policy.json")
}

resource "aws_iam_role_policy_attachment" "readonly" {
  role       = aws_iam_role.agent.name
  policy_arn = aws_iam_policy.readonly.arn
}

# Write its own logs — the only unconditional write the agent has.
resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.agent.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Read exactly one secret: the agent's own API key.
data "aws_iam_policy_document" "secret_access" {
  statement {
    sid       = "ReadOwnApiKeyOnly"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [local.anthropic_secret_arn]
  }
}

resource "aws_iam_policy" "secret_access" {
  name   = "${var.project_name}-secret-access"
  policy = data.aws_iam_policy_document.secret_access.json
}

resource "aws_iam_role_policy_attachment" "secret_access" {
  role       = aws_iam_role.agent.name
  policy_arn = aws_iam_policy.secret_access.arn
}

# Publish the scheduled report to exactly one topic.
data "aws_iam_policy_document" "publish_report" {
  count = var.enable_schedule ? 1 : 0

  statement {
    sid       = "PublishReportToOwnTopicOnly"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.reports[0].arn]
  }
}

resource "aws_iam_policy" "publish_report" {
  count  = var.enable_schedule ? 1 : 0
  name   = "${var.project_name}-publish-report"
  policy = data.aws_iam_policy_document.publish_report[0].json
}

resource "aws_iam_role_policy_attachment" "publish_report" {
  count      = var.enable_schedule ? 1 : 0
  role       = aws_iam_role.agent.name
  policy_arn = aws_iam_policy.publish_report[0].arn
}
