output "ecr_repository_url" {
  description = "Push the agent image here."
  value       = aws_ecr_repository.agent.repository_url
}

output "lambda_function_name" {
  value = aws_lambda_function.agent.function_name
}

output "function_url" {
  description = "IAM-authenticated endpoint. Call it with SigV4, e.g. `awscurl` or `aws lambda invoke`."
  value       = try(aws_lambda_function_url.agent[0].function_url, "disabled")
}

output "anthropic_secret_arn" {
  value = local.anthropic_secret_arn
}

output "report_topic_arn" {
  value = try(aws_sns_topic.reports[0].arn, "disabled")
}

output "role_arn" {
  description = "The agent's read-only execution role."
  value       = aws_iam_role.agent.arn
}
