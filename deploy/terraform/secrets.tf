locals {
  create_secret        = var.anthropic_secret_arn == ""
  anthropic_secret_arn = local.create_secret ? aws_secretsmanager_secret.anthropic[0].arn : var.anthropic_secret_arn
}

resource "aws_secretsmanager_secret" "anthropic" {
  count                   = local.create_secret ? 1 : 0
  name                    = var.anthropic_secret_name != "" ? var.anthropic_secret_name : "${var.project_name}/anthropic-api-key"
  description             = "Anthropic API key used by the AWS monitoring agent"
  recovery_window_in_days = 7
}

# Only written when you pass the key through Terraform. Leaving var.anthropic_api_key
# empty creates the secret shell and you fill it with:
#   aws secretsmanager put-secret-value --secret-id <name> --secret-string sk-ant-...
resource "aws_secretsmanager_secret_version" "anthropic" {
  count         = local.create_secret && var.anthropic_api_key != "" ? 1 : 0
  secret_id     = aws_secretsmanager_secret.anthropic[0].id
  secret_string = var.anthropic_api_key
}
