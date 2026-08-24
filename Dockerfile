# Lambda container image (STAGE 4).
#
# The agent collects everything through the AWS CLI, so the CLI has to be in the
# image. The official Lambda Python base image gives us the runtime interface
# client; we add AWS CLI v2 and the agent itself.

FROM public.ecr.aws/lambda/python:3.12

RUN dnf install -y unzip tar gzip \
    && curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip \
    && unzip -q /tmp/awscliv2.zip -d /tmp \
    && /tmp/aws/install --bin-dir /usr/local/bin --install-dir /usr/local/aws-cli \
    && rm -rf /tmp/awscliv2.zip /tmp/aws \
    && dnf clean all \
    && aws --version

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY agent/ ${LAMBDA_TASK_ROOT}/agent/
COPY knowledge/ ${LAMBDA_TASK_ROOT}/knowledge/

# /var/task is read-only at runtime; state and the audit log go to /tmp.
ENV AGENT_STATE_DIR=/tmp/agent-state \
    AGENT_KNOWLEDGE_DIR=${LAMBDA_TASK_ROOT}/knowledge \
    AGENT_JSON_LOGS=true \
    AGENT_APPROVAL_MODE=deny \
    PYTHONUNBUFFERED=1

CMD ["agent.lambda_handler.handler"]
