resource "aws_sns_topic" "reports" {
  count = var.enable_schedule ? 1 : 0
  name  = "${var.project_name}-reports"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.enable_schedule && var.report_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.reports[0].arn
  protocol  = "email"
  endpoint  = var.report_email
}

resource "aws_cloudwatch_event_rule" "report" {
  count               = var.enable_schedule ? 1 : 0
  name                = "${var.project_name}-schedule"
  description         = "Scheduled read-only AWS status report"
  schedule_expression = var.schedule_expression
}

resource "aws_cloudwatch_event_target" "report" {
  count     = var.enable_schedule ? 1 : 0
  rule      = aws_cloudwatch_event_rule.report[0].name
  target_id = "agent"
  arn       = aws_lambda_function.agent.arn
  input     = jsonencode({ action = "report", include_security = true })
}

resource "aws_lambda_permission" "events" {
  count         = var.enable_schedule ? 1 : 0
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.agent.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.report[0].arn
}

# STAGE 8: alarm on the agent's own failures. A monitor nobody monitors is a
# monitor you cannot trust.
resource "aws_cloudwatch_metric_alarm" "agent_errors" {
  alarm_name          = "${var.project_name}-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 3600
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "The monitoring agent itself failed to run"
  treat_missing_data  = "notBreaching"
  dimensions          = { FunctionName = aws_lambda_function.agent.function_name }
  alarm_actions       = aws_sns_topic.reports[*].arn
}
