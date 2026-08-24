variable "project_name" {
  description = "Name prefix for every resource this module creates."
  type        = string
  default     = "aws-monitoring-agent"
}

variable "aws_region" {
  description = "Region the agent runs in and inspects by default."
  type        = string
  default     = "us-east-1"
}

variable "image_tag" {
  description = "Container image tag to deploy. `make deploy` pushes and sets this."
  type        = string
  default     = "latest"
}

variable "anthropic_secret_name" {
  description = "Secrets Manager secret holding the Anthropic API key. Created by this module unless anthropic_secret_arn is set."
  type        = string
  default     = ""
}

variable "anthropic_secret_arn" {
  description = "ARN of an existing Secrets Manager secret holding the API key. Leave empty to have this module create one."
  type        = string
  default     = ""
}

variable "anthropic_api_key" {
  description = "API key value, used only when this module creates the secret. Prefer setting it out-of-band and leaving this empty."
  type        = string
  default     = ""
  sensitive   = true
}

variable "agent_model" {
  description = "Claude model id the deployed agent uses."
  type        = string
  default     = "claude-opus-5"
}

variable "approval_mode" {
  description = "Sensitive-read policy for the deployed agent: deny (recommended), ask or allow."
  type        = string
  default     = "deny"

  validation {
    condition     = contains(["deny", "ask", "allow"], var.approval_mode)
    error_message = "approval_mode must be one of: deny, ask, allow."
  }
}

variable "lambda_memory_mb" {
  description = "Lambda memory. The AWS CLI in the image needs headroom; 1024 is a sane floor."
  type        = number
  default     = 1024
}

variable "lambda_timeout_seconds" {
  description = "Lambda timeout. A full report with several tool calls can take a minute."
  type        = number
  default     = 300
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the agent's own logs."
  type        = number
  default     = 30
}

variable "enable_function_url" {
  description = "Expose an IAM-authenticated HTTPS endpoint for the agent."
  type        = bool
  default     = true
}

variable "enable_schedule" {
  description = "Run the status report on a schedule."
  type        = bool
  default     = true
}

variable "schedule_expression" {
  description = "EventBridge schedule for the report (UTC)."
  type        = string
  default     = "cron(0 7 ? * MON-FRI *)"
}

variable "report_email" {
  description = "Address subscribed to the report topic. Empty means no email delivery."
  type        = string
  default     = ""
}
