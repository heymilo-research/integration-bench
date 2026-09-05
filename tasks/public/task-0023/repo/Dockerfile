FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
RUN mkdir -p /app/output

# Scenarios invoke this container with an explicit subcommand (backfill / poll
# / serve / dump). The default command runs the webhook listener so that
# `docker compose up app` (used to give the listener the stable `connector`
# network alias) starts serving without extra arguments.
CMD ["python", "-m", "talentloop_deletes", "serve"]
