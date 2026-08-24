# ===========================================================================
#  AWS Monitoring Agent — one Makefile for all eight stages.
#
#  New here?   make quickstart
#  Lost?       make help
#  Suspicious? make policy      (shows exactly what the agent may and may not do)
# ===========================================================================

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

VENV      := .venv
PY        := $(VENV)/bin/python
PIP       := $(VENV)/bin/pip
STAMP     := $(VENV)/.installed
AGENT     := $(PY) -m agent
TF_DIR    := deploy/terraform
IMAGE     := aws-monitoring-agent
PROJECT   ?= aws-monitoring-agent
REGION    ?= $(shell grep -E '^AWS_REGION=' .env 2>/dev/null | cut -d= -f2)
REGION    := $(if $(strip $(REGION)),$(strip $(REGION)),us-east-1)
Q         ?= What is the overall health of this AWS account right now?

export AWS_REGION := $(REGION)

.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
.PHONY: help
help: ## Show this help
	@echo ""
	@echo "  AWS Monitoring Agent — read-only status, health and inventory"
	@echo "  ---------------------------------------------------------------"
	@echo "  First run:  make quickstart      Everything up to your first answer"
	@echo "  Daily use:  make report          Full status report"
	@echo "              make ask Q=\"...\"     Ask the agent anything"
	@echo ""
	@awk 'BEGIN {FS = ":.*?## "} \
		/^##@/ { printf "\n  \033[1m%s\033[0m\n", substr($$0, 5); next } \
		/^[a-zA-Z_-]+:.*?## / { printf "    \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo ""
	@echo "  Variables:  Q=\"question\"  REGION=eu-west-1  PROJECT=name"
	@echo ""

.PHONY: quickstart
quickstart: setup doctor demo ## STAGES 1-3: set up, verify, and run the agent once
	@echo ""
	@echo "  Ready. Try:  make chat        (conversation)"
	@echo "               make report      (full status report)"
	@echo "               make ask Q=\"how many EC2 instances are running?\""
	@echo ""

.PHONY: all
all: setup doctor test report ## Set up, verify, test, then produce a report

# ===========================================================================
##@ STAGE 1 — Computer + AWS setup
# ===========================================================================

$(STAMP): requirements.txt requirements-dev.txt
	@echo "==> creating virtualenv in $(VENV)"
	@test -d $(VENV) || python3 -m venv $(VENV)
	@$(PIP) install --quiet --upgrade pip
	@$(PIP) install --quiet -r requirements.txt -r requirements-dev.txt
	@touch $(STAMP)
	@echo "==> python dependencies installed"

.PHONY: setup
setup: $(STAMP) .env ## Create the virtualenv, install dependencies, seed .env
	@echo ""
	@echo "  Setup complete. Next:"
	@echo "    1. put your Anthropic key in .env  (ANTHROPIC_API_KEY=sk-ant-...)"
	@echo "    2. make sure the AWS CLI can see your account:  aws configure"
	@echo "    3. make doctor"
	@echo ""

.env: .env.example
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "==> created .env from .env.example — add your ANTHROPIC_API_KEY"; \
	else \
		echo "==> .env already exists, leaving it alone"; \
		touch .env; \
	fi

.PHONY: setup-server
setup-server: $(STAMP) ## Also install the HTTP API dependencies (fastapi, uvicorn)
	@$(PIP) install --quiet -r requirements-server.txt
	@echo "==> server dependencies installed"

.PHONY: install-awscli
install-awscli: ## Install AWS CLI v2 into ~/.local (Linux/macOS, no root needed)
	@bash scripts/install_awscli.sh

.PHONY: aws-configure
aws-configure: ## Run `aws configure` for the read-only credentials
	@command -v aws >/dev/null || { echo "aws CLI not found — run: make install-awscli"; exit 1; }
	@aws configure

.PHONY: doctor
doctor: $(STAMP) ## Verify this machine is ready (python, CLI, credentials, key)
	@$(AGENT) doctor

.PHONY: whoami
whoami: ## Show which AWS identity the agent is using
	@aws sts get-caller-identity --output table --no-cli-pager

# ===========================================================================
##@ STAGE 2 — The agent itself
# ===========================================================================

.PHONY: hello
hello: $(STAMP) ## Talk to Claude with no AWS access at all (proves stage 2 works)
	@$(AGENT) ask --no-tools "In two sentences, introduce yourself as a read-only AWS monitoring agent."

.PHONY: chat
chat: $(STAMP) ## Interactive conversation with memory
	@$(AGENT) chat --session $${SESSION:-default}

.PHONY: ask
ask: $(STAMP) ## Ask one question:  make ask Q="how many S3 buckets do I have?"
	@$(AGENT) ask --session $${SESSION:-default} "$(Q)"

.PHONY: demo
demo: $(STAMP) ## Run the agent once against your account
	@$(AGENT) ask "$(Q)" || echo "  (demo needs ANTHROPIC_API_KEY and AWS credentials — see make doctor)"

# ===========================================================================
##@ STAGE 3 — AWS tools (read-only, via the AWS CLI)
# ===========================================================================

.PHONY: tools
tools: $(STAMP) ## List the tools the agent can call
	@$(AGENT) tools

.PHONY: inventory
inventory: $(STAMP) ## Count every resource visible in the configured region
	@$(AGENT) inventory

.PHONY: health
health: $(STAMP) ## Run the health sweep (alarms, EC2, RDS, ASG, ELB, ECS)
	@$(AGENT) health

.PHONY: run-tool
run-tool: $(STAMP) ## Call one tool directly:  make run-tool TOOL=aws_health INPUT='{}'
	@$(AGENT) run-tool $(TOOL) --input '$(or $(INPUT),{})'

# ===========================================================================
##@ STAGE 4 — Deploy to AWS
# ===========================================================================

.PHONY: docker-build
docker-build: ## Build the Lambda container image
	@docker build -t $(IMAGE):latest .

.PHONY: docker-build-server
docker-build-server: ## Build the long-running HTTP server image
	@docker build -f Dockerfile.server -t $(IMAGE)-server:latest .

.PHONY: serve
serve: setup-server ## Run the HTTP API locally on :8080
	@$(AGENT) serve --port $${PORT:-8080}

.PHONY: up
up: ## Run the HTTP API in Docker (reads .env)
	@docker compose up --build

.PHONY: down
down: ## Stop the Docker stack
	@docker compose down

.PHONY: tf-init
tf-init: ## terraform init
	@cd $(TF_DIR) && terraform init

.PHONY: tf-plan
tf-plan: tf-init ## Show what deploying would create
	@cd $(TF_DIR) && terraform plan -var="aws_region=$(REGION)" -var="project_name=$(PROJECT)"

.PHONY: deploy
deploy: ## Build, push and deploy the agent to AWS Lambda
	@bash scripts/deploy.sh "$(PROJECT)" "$(REGION)"

.PHONY: invoke
invoke: ## Invoke the deployed agent and print its report
	@bash scripts/invoke.sh "$(PROJECT)" "$(REGION)"

.PHONY: deployed-logs
deployed-logs: ## Tail the deployed agent's CloudWatch logs
	@aws logs tail "/aws/lambda/$(PROJECT)" --since 1h --follow --region $(REGION)

.PHONY: destroy
destroy: ## Remove everything this project created in AWS
	@cd $(TF_DIR) && terraform destroy -var="aws_region=$(REGION)" -var="project_name=$(PROJECT)"

# ===========================================================================
##@ STAGE 5 — DevOps capabilities
# ===========================================================================

.PHONY: report
report: $(STAMP) ## Full status report: inventory + health + cost
	@$(AGENT) report

.PHONY: report-full
report-full: $(STAMP) ## Status report including the security review
	@$(AGENT) report --security

.PHONY: cost
cost: $(STAMP) ## Spend for the last $${DAYS:-7} days, by service
	@$(AGENT) cost --days $${DAYS:-7}

.PHONY: logs
logs: $(STAMP) ## Search CloudWatch Logs:  make logs GROUP=/aws/lambda/x PATTERN=ERROR
	@$(AGENT) logs $(if $(GROUP),--log-group $(GROUP),) --pattern "$${PATTERN:-ERROR}" --minutes $${MINUTES:-60}

.PHONY: security
security: $(STAMP) ## Read-only security posture review
	@$(AGENT) security

.PHONY: triage
triage: $(STAMP) ## Point the agent at whatever is currently unhealthy
	@$(AGENT) ask --session triage "Run a health check. For anything degraded, investigate with metrics and logs, check the runbooks, and tell me the impact and the exact human action required."

# ===========================================================================
##@ STAGE 6 — Memory + knowledge
# ===========================================================================

.PHONY: knowledge
knowledge: $(STAMP) ## Search the runbooks:  make knowledge QUERY="rds storage"
	@$(AGENT) knowledge "$(QUERY)"

.PHONY: facts
facts: $(STAMP) ## Show everything the agent remembers
	@$(AGENT) memory list

.PHONY: remember
remember: $(STAMP) ## Teach it a fact:  make remember KEY=owner VALUE="team-a owns prod"
	@$(AGENT) memory add --key "$(KEY)" --value "$(VALUE)"

.PHONY: forget
forget: $(STAMP) ## Remove a fact:  make forget KEY=owner
	@$(AGENT) memory forget --key "$(KEY)"

.PHONY: changes
changes: $(STAMP) ## What appeared or disappeared since the last inventory
	@$(AGENT) run-tool aws_inventory_changes

# ===========================================================================
##@ STAGE 7 — Security + approvals
# ===========================================================================

.PHONY: policy
policy: $(STAMP) ## Show what the read-only policy engine allows and denies
	@$(AGENT) policy

.PHONY: policy-check
policy-check: $(STAMP) ## Check one command:  make policy-check CMD="ec2 describe-vpcs"
	@$(AGENT) policy $(CMD)

.PHONY: audit
audit: $(STAMP) ## Tail the audit log — every call the agent has made
	@$(AGENT) audit --limit $${LIMIT:-30}

.PHONY: iam-policy
iam-policy: ## Print the IAM policy to attach to the agent's credentials
	@cat deploy/iam/agent-readonly-policy.json

.PHONY: verify-readonly
verify-readonly: $(STAMP) ## Prove the write boundary holds (runs the guard test suite)
	@$(PY) -m pytest tests/test_guard.py tests/test_awscli.py -q
	@echo ""
	@$(AGENT) policy

# ===========================================================================
##@ STAGE 8 — Production hardening
# ===========================================================================

.PHONY: test
test: $(STAMP) ## Run the test suite
	@$(PY) -m pytest tests -q

.PHONY: test-verbose
test-verbose: $(STAMP) ## Run the test suite with names
	@$(PY) -m pytest tests -v

.PHONY: lint
lint: $(STAMP) ## Lint with ruff
	@$(VENV)/bin/ruff check agent tests

.PHONY: fmt
fmt: $(STAMP) ## Format with ruff
	@$(VENV)/bin/ruff format agent tests

.PHONY: ci
ci: lint test ## What CI runs: lint + tests
	@echo "==> CI checks passed"

.PHONY: clean
clean: ## Remove caches and local state (keeps .env and the venv)
	@rm -rf .pytest_cache .ruff_cache var/agent.db var/audit.jsonl
	@find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "==> cleaned"

.PHONY: clean-all
clean-all: clean ## Also remove the virtualenv
	@rm -rf $(VENV)
	@echo "==> removed $(VENV)"
