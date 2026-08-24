"""Read-only AWS monitoring agent.

Stage map (see README.md):
  1  config / doctor      — machine + AWS setup checks
  2  llm / prompt / loop  — the agent itself
  3  awscli / tools       — AWS read-only tools, collected via the AWS CLI
  4  server / deploy      — running it on AWS
  5  tools.devops         — cost, logs, security, reports
  6  memory / knowledge   — durable facts and runbook retrieval
  7  guard / approvals    — policy engine, human-in-the-loop, audit trail
  8  tests / observability— production hardening
"""

__version__ = "1.0.0"
