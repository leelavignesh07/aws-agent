# Naming conventions

Resources in this account follow `<app>-<env>-<component>`, e.g.
`crm-prod-web`, `crm-staging-db`. Anything that does not match the pattern is
either legacy or created outside Terraform — flag it in inventory reports.

# Environments

* `prod`    — customer-facing. Any alarm here is page-worthy.
* `staging` — pre-production. Alarms are informational.
* `sandbox` — experiments. Expected to be noisy; exclude from health scoring.

# Ownership

Tag `Owner` carries the team email. Untagged resources have no owner and should
be listed separately in every inventory report.

# What this agent may not do

This agent holds read-only credentials. It never creates, modifies, deletes,
starts, stops or restarts anything. When a runbook step requires a write, the
agent stops and names the human action required.
