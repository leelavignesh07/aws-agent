# AWS Monitoring Agent

A read-only AI agent that answers questions about your AWS account — what exists,
whether it is healthy, what it costs — by running AWS CLI commands and reasoning
over the results.

It can look at everything it is allowed to see. It cannot create, modify, delete,
start, stop or restart anything. That is enforced in three independent places,
not by asking the model nicely (see [Security model](#security-model)).

```
  you ──▶ make ask Q="is anything broken?"
             │
             ▼
        Claude (claude-opus-5)
             │  chooses tools
             ▼
        policy engine  ──▶ DENY anything that is not provably a read
             │ ALLOW
             ▼
        aws cli  ──▶  AWS  ──▶  JSON  ──▶  answer + audit trail
```

---

## Quickstart

```bash
git clone https://github.com/leelavignesh07/aws-agent
cd aws-agent
make quickstart
```

That runs stages 1–3: creates the virtualenv, installs dependencies, seeds `.env`,
checks your machine, and asks the agent its first question. Two things you supply:

1. **An Anthropic API key** in `.env` → `ANTHROPIC_API_KEY=sk-ant-...`
2. **AWS credentials** the CLI can see — `aws configure`, an SSO profile, or an
   instance role. Attach the read-only policy in
   [`deploy/iam/agent-readonly-policy.json`](deploy/iam/agent-readonly-policy.json).

No AWS CLI yet? `make install-awscli`. Then `make doctor` tells you exactly what
is still missing and the command that fixes it.

Everything here runs on bash and python. Nothing to install beyond Python 3.10+,
the AWS CLI, and — only if you deploy — Terraform.

```bash
make report                                   # full status report
make ask Q="how many EC2 instances are running, and are any impaired?"
make chat                                     # conversation, with memory
make policy                                   # what the agent may and may not do
```

---

## The eight stages

Each stage is a working increment with its own make targets. Everything below is
already implemented — the stages are how you *learn* the codebase, not a backlog.

### Stage 1 — Computer + AWS setup
`make setup` · `make install-awscli` · `make aws-configure` · `make doctor` · `make whoami`

Creates `.venv`, installs dependencies, copies `.env.example` to `.env`, and
verifies eight prerequisites (Python version, SDK, API key, AWS CLI v2, working
credentials, region, approval mode, writable state directory, knowledge base).
`make doctor` exits non-zero until the machine is genuinely ready.

*Code:* `agent/config.py`, `agent/doctor.py`, `scripts/install_awscli.sh`

### Stage 2 — Simple Python AI agent
`make hello` · `make chat` · `make ask Q="..."`

The agent loop: a streamed Messages API call, adaptive thinking, and a manual
tool loop that keeps its own history so it can also write the audit trail and
persist memory. `make hello` runs it with **no AWS access at all**, which is the
honest way to see that stage 2 works before stage 3 exists.

*Code:* `agent/llm.py`, `agent/prompt.py`, `agent/loop.py`

### Stage 3 — Give the agent AWS tools
`make tools` · `make inventory` · `make health` · `make run-tool TOOL=...`

Eleven tools, all reading through the AWS CLI:

| Tool | What it answers |
|---|---|
| `aws_inventory` | How many of everything, across 35 resource types |
| `aws_health` | Alarms, EC2 status checks, RDS, ASG capacity, ELB targets, ECS |
| `aws_metric` | CloudWatch statistics for one metric over a window |
| `aws_cost` | Spend by service, with day-over-day trend |
| `aws_logs` | Log groups, and filtered events from any of them |
| `aws_security_posture` | Open security groups, public buckets/RDS, stale keys |
| `aws_cli` | The long tail — any read-only call, screened by the policy engine |
| `aws_inventory_changes` | What appeared or disappeared since the last snapshot |
| `remember` / `recall` | Durable facts about the account |
| `search_knowledge` | Your runbooks |

Everything is collected by shelling out to `aws`, never an SDK — so every action
the agent takes is a command you can paste into your own terminal, and the audit
log reads like a shell history.

*Code:* `agent/awscli.py`, `agent/tools/`

### Stage 4 — Deploy the agent to AWS
`make package` · `make serve` · `make tf-plan` · `make deploy` · `make invoke` · `make destroy`

**No Docker anywhere.** The build is bash and python: `scripts/build_lambda.sh`
pip-installs the dependencies into `dist/lambda/`, copies the agent and the
runbooks in, and zips it with python's `zipfile`. There is no image, no
registry and nothing to log in to — so `make deploy` is a single
`terraform apply` that uploads the code as it goes.

Two deployment shapes from one codebase:

* **Lambda zip** (`scripts/build_lambda.sh`, `deploy/terraform/`) — Lambda
  function with the read-only role, IAM-authenticated Function URL, CloudWatch
  log group with retention, an EventBridge schedule for the daily report, an SNS
  topic for delivery, and an alarm on the agent's own failures.
* **HTTP service** (`make serve`) — FastAPI on :8080 for EC2, a systemd unit, or
  anything else that runs a process; bearer-token authenticated, with `/metrics`
  for Prometheus. `make serve-bg` / `make serve-stop` run it in the background.

A Lambda zip has no AWS CLI, and the agent collects everything by shelling out
to `aws` — so the package carries its own copy: the pip-installable AWS CLI v1,
plus a `bin/aws` shim, and the function is pointed at it with `AGENT_AWS_BIN`.
(AWS CLI v2 ships only as a ~270 MB unpacked bundle, over Lambda's 250 MB limit.
Locally you still want v2: `make install-awscli`.) The finished package is about
25 MB zipped and 145 MB unpacked, and the script fails the build if either
number crosses a Lambda limit.

Compiled wheels are cross-built with pip's `--platform` / `--python-version`, so
the package is correct for Lambda's `linux/x86_64` even when you build it on a
Mac. `ARCH=arm64 make package` builds for Graviton — set
`lambda_architecture = "arm64"` in `terraform.tfvars` to match.

*Code:* `agent/server.py`, `agent/lambda_handler.py`, `deploy/`,
`scripts/build_lambda.sh`, `scripts/deploy.sh`

### Stage 5 — DevOps capabilities
`make report` · `make report-full` · `make cost` · `make logs` · `make security` · `make triage`

Composite reports that run **without the model** — a scheduled status report
should not fail because an LLM is unreachable. `make triage` is the opposite: it
points the model at whatever is currently degraded and asks for impact and the
exact human action required.

*Code:* `agent/reports.py`, `agent/tools/devops.py`

### Stage 6 — Memory + knowledge
`make facts` · `make remember KEY=... VALUE="..."` · `make knowledge QUERY="..."` · `make changes`

SQLite for conversation history, durable facts and inventory snapshots. Markdown
runbooks under `knowledge/`, retrieved with BM25 — no vector database, no
embedding service, no extra dependency. Drop your own runbooks in that directory
and the agent starts citing them.

*Code:* `agent/memory.py`, `agent/knowledge.py`, `knowledge/`

### Stage 7 — Security + approvals
`make policy` · `make policy-check CMD="..."` · `make audit` · `make iam-policy` · `make verify-readonly`

The policy engine, the human-in-the-loop gate and the audit trail. `make policy`
prints the decision for a representative set of commands so you can see the
boundary rather than trust it.

*Code:* `agent/guard.py`, `agent/approvals.py`, `agent/audit.py`, `deploy/iam/`

### Stage 8 — Production-grade platform
`make test` · `make lint` · `make ci` · `make clean`

135 tests, none of which need AWS credentials or network access (a fake `aws`
binary stands in). Ruff for lint and format. Structured JSON logging, Prometheus
metrics, a CloudWatch alarm on the agent itself, log retention, and a GitHub
Actions workflow in `ci.yml.template` that runs lint, format, tests,
`terraform validate` and a package build (copy it to `.github/workflows/ci.yml`
to enable it — see the header for why it ships as a template). CI needs no
Docker either: it builds the same zip `make deploy` does.

---

## Security model

Three independent layers. Any one of them alone would stop a write; all three
have to fail for anything to change in your account.

**1. The policy engine (`agent/guard.py`)** — every call is classified before it
runs, by verb and by an explicit rule table:

| Verdict | Meaning | Examples |
|---|---|---|
| `ALLOW` | Runs immediately | `describe-*`, `list-*`, `get-*`, `lookup-*` |
| `SENSITIVE` | Needs a human yes | `iam get-credential-report`, `s3api get-object`, `lambda get-function` |
| `DENY` | Never runs | every mutating verb, `sts assume-role`, `secretsmanager get-secret-value`, `ssm get-parameter`, `ecr get-login-password`, `aws s3 cp/rm/sync` |

An operation whose verb nobody has classified is **denied**, not allowed — the
default is closed. Flags that could redirect a call (`--endpoint-url`,
`--cli-input-json`, `--no-verify-ssl`) are rejected, and the CLI is invoked with
an argv list, never a shell string.

**2. IAM** — `deploy/iam/agent-readonly-policy.json` grants Describe/List/Get
actions and adds an explicit `Deny` on everything that vends credentials or
object data. Even if layer 1 were bypassed, AWS refuses the call.

**3. Approvals and audit** — `AGENT_APPROVAL_MODE` decides what happens to a
`SENSITIVE` call: `ask` (prompt on the terminal), `deny` (refuse — the default
for servers and CI), `allow` (break-glass, loudly audited). A non-interactive
session in `ask` mode fails closed rather than hanging or auto-approving. Every
decision, call, result and approval is appended to `var/audit.jsonl` **before**
execution, so even a crash leaves a record of intent.

The deployed agent has exactly one write permission: `sns:Publish` to its own
report topic, and only when you enable the schedule.

Verify all of this yourself:

```bash
make verify-readonly     # runs the guard test suite, then prints the boundary
make policy-check CMD="ec2 terminate-instances"
```

---

## Configuration

Everything lives in `.env` (see `.env.example` for the annotated list).

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for anything involving the model |
| `AGENT_MODEL` | `claude-opus-5` | Model id |
| `AGENT_EFFORT` | `high` | Thinking depth: `low`…`max` |
| `AGENT_MAX_ITERATIONS` | `24` | Tool-loop budget per question |
| `AWS_REGION` | `us-east-1` | Region the agent inspects |
| `AWS_PROFILE` | — | Empty uses the default credential chain |
| `AGENT_AWS_BIN` | — | Path to `aws`; empty means "find it on PATH" |
| `AGENT_APPROVAL_MODE` | `ask` | `ask` / `deny` / `allow` |
| `AGENT_CACHE_TTL` | `60` | Seconds an identical read is reused |
| `AGENT_CLI_TIMEOUT` | `90` | Per-call timeout |
| `AGENT_API_TOKEN` | — | Required by `make serve` |
| `AGENT_SHOW_THINKING` | `false` | Stream the reasoning summary |
| `AGENT_REDACT_ACCOUNT_ID` | `false` | Mask account ids in the audit log |

Override per invocation: `make ask Q="..." REGION=eu-west-1`.

---

## What it costs

* **Claude** — a typical `make report`-shaped question is a few cents; the CLI
  prints an estimate after every run, and every answer is audited with its token
  usage. Lower it with `AGENT_EFFORT=medium` or `AGENT_MODEL=claude-sonnet-5`.
* **AWS** — the read calls are free, with one exception: Cost Explorer charges
  about $0.01 per `get-cost-and-usage` request, so `make cost` is not free.
* **Deployed** — Lambda for a weekday report is cents per month; the code is a
  25 MB zip stored with the function; logs expire after 30 days by default.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `make doctor` fails on credentials | `aws configure`, or check `AWS_PROFILE` in `.env` |
| `the anthropic package is not installed` | `make setup` |
| Everything reports `access_denied` | The IAM identity lacks read permissions — attach `make iam-policy` output |
| `DENIED by policy` on a read you want | Check `make policy-check CMD="<service> <operation>"`; if it is genuinely a read, add it to `ALLOW_EXTRA` in `agent/guard.py` |
| Sensitive read refused in CI | Expected. `AGENT_APPROVAL_MODE=deny` is the correct server default |
| `AGENT_API_TOKEN is not set` | `make serve` needs a token: `openssl rand -hex 32` |
| Cost Explorer returns an error | Enable Cost Explorer once in the console; it takes ~24h to populate |
| `make package` fails on a size limit | A dependency grew. Trim `requirements.txt`, or upload the zip through S3 |
| Deployed agent says `aws` is not on PATH | Rebuild and redeploy with `make deploy` — the zip carries its own CLI at `/var/task/bin/aws` |

---

## Extending it

* **A new tool** — write a function in `agent/tools/`, decorate it with `@tool`,
  and it is immediately available to the model, the CLI (`make run-tool`) and the
  HTTP API. Keep the description specific: it is what the model uses to choose.
* **A new runbook** — drop a markdown file in `knowledge/`. Headings become
  retrievable passages.
* **A new region** — `make report REGION=eu-west-1`. Nothing is hardcoded.
* **A read the guard refuses** — add it to `ALLOW_EXTRA` (or `SENSITIVE_CALLS`)
  in `agent/guard.py`, with a test. Never widen `READ_VERBS` casually: that table
  is the boundary.

## Layout

```
.
├── Makefile                  every stage, one file
├── agent/
│   ├── guard.py              STAGE 7  the read-only policy engine
│   ├── awscli.py             STAGE 3  guarded AWS CLI executor
│   ├── approvals.py          STAGE 7  human-in-the-loop gate
│   ├── audit.py              STAGE 7  append-only audit trail
│   ├── llm.py                STAGE 2  Claude client + usage accounting
│   ├── prompt.py             STAGE 2  system prompt
│   ├── loop.py               STAGE 2  the agent loop
│   ├── memory.py             STAGE 6  SQLite memory and snapshots
│   ├── knowledge.py          STAGE 6  BM25 runbook retrieval
│   ├── reports.py            STAGE 5  composite reports (no model needed)
│   ├── doctor.py             STAGE 1  environment checks
│   ├── server.py             STAGE 4  FastAPI
│   ├── lambda_handler.py     STAGE 4  Lambda entry point
│   ├── secrets.py            STAGE 4  bootstraps its own API key
│   ├── cli.py                the command line
│   └── tools/                STAGE 3/5  the tools themselves
├── deploy/
│   ├── iam/                  read-only policy + trust policies
│   └── terraform/            Lambda, IAM, schedule, alarms
├── knowledge/                runbooks the agent can cite
├── scripts/
│   ├── install_awscli.sh     STAGE 1  AWS CLI v2 into ~/.local
│   ├── build_lambda.sh       STAGE 4  the deployment zip — bash + python
│   ├── deploy.sh             STAGE 4  package, then terraform apply
│   └── invoke.sh             STAGE 4  call the deployed function
└── tests/                    135 tests, no AWS required
```
