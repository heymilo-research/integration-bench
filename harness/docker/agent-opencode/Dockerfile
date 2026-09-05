FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
      bash ca-certificates curl git jq python3 python3-pip python3-venv \
 && ln -sf /usr/bin/python3 /usr/local/bin/python \
 && rm -rf /var/lib/apt/lists/*

ARG OPENCODE_VERSION=1.18.18
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y -qq --no-install-recommends nodejs \
 && npm install -g "opencode-ai@${OPENCODE_VERSION}" \
 && npm cache clean --force \
 && rm -rf /var/lib/apt/lists/*
LABEL ib.agent.harnesses="opencode" \
      ib.agent.opencode_version="${OPENCODE_VERSION}"

RUN useradd -m -s /bin/bash -u 1001 agent
USER agent
WORKDIR /workspace
CMD ["sleep", "infinity"]
