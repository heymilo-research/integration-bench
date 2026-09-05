FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# without burning turns on apt. jq helps quick JSON probes in bash.
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
      bash ca-certificates curl git jq \
      python3 python3-pip python3-venv \
 && ln -sf /usr/bin/python3 /usr/local/bin/python \
 && rm -rf /var/lib/apt/lists/*

# Node 20 + Claude Code CLI: this image doubles as the subscription harness
# agent (`bench eval --harness claude-code`), which drives `claude` headless.
# Pinning prevents a local rebuild from changing the evaluated harness.
ARG CLAUDE_CODE_VERSION=2.1.232
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y -qq --no-install-recommends nodejs \
 && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force \
 && rm -rf /var/lib/apt/lists/*
LABEL ib.agent.harnesses="direct,claude-code" \
      ib.agent.claude_code_version="${CLAUDE_CODE_VERSION}"

# Non-root user: `claude --dangerously-skip-permissions` refuses to run as
# root, and containment (not the prompt) is the isolation boundary. uid 1001
# avoids clashing with ubuntu 24.04's default uid-1000 `ubuntu` user.
RUN useradd -m -s /bin/bash -u 1001 agent
USER agent

WORKDIR /workspace
CMD ["sleep", "infinity"]
