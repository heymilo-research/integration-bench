FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Deliberately a SEPARATE Dockerfile from docker/agent/, not a shared one with a
# conditional install. `ensure_agent_image()` decides staleness by hashing the
# Dockerfile BYTES and comparing against the image's `ib.agent.dockerfile_sha`
# label, so a single shared file means any edit for one lane silently rebuilds
# the other lane's image on its next rollout — mid-sweep, with no error and no
# log line. Two files, two tags, two hashes: a codex change can never invalidate
# `ib-agent:local` while a Claude Code sweep is running.
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
      bash ca-certificates curl git jq \
      python3 python3-pip python3-venv \
 && ln -sf /usr/bin/python3 /usr/local/bin/python \
 && rm -rf /var/lib/apt/lists/*

# Node 20 + Codex CLI. The npm package is a thin launcher that resolves a
# platform-specific optional dependency (@openai/codex-linux-x64 /
# -linux-arm64), so this works on both amd64 and arm64 hosts.
#
# PINNED deliberately. An unpinned `npm install -g @openai/codex` resolves to
# whatever is latest at build time, so an image rebuilt halfway through a sweep
# silently changes the agent under test — the benchmark's own independent
# variable. Observed immediately: the host CLI was 0.146.0 while an unpinned
# build of this image produced 0.147.0. Bump this ARG explicitly, record it in
# the WORKLOG, and re-baseline; never let it drift.
ARG CODEX_VERSION=0.147.0
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y -qq --no-install-recommends nodejs \
 && npm install -g "@openai/codex@${CODEX_VERSION}" \
 && npm cache clean --force \
 && rm -rf /var/lib/apt/lists/*
LABEL ib.agent.harnesses="codex" \
      ib.agent.codex_version="${CODEX_VERSION}"

# Non-root user, matching docker/agent/. Codex does not refuse to run as root
# the way `claude --dangerously-skip-permissions` does, but containment — not
# the CLI's own check — is the isolation boundary here, and the uid must match
# so the two lanes produce byte-identical file ownership in the graded diff.
# uid 1001 avoids clashing with ubuntu 24.04's default uid-1000 `ubuntu` user.
RUN useradd -m -s /bin/bash -u 1001 agent
USER agent

WORKDIR /workspace
CMD ["sleep", "infinity"]
