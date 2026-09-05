FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
RUN mkdir -p /app/output

# The task's scenarios invoke this container with an explicit subcommand
# (backfill / serve / push / dump). The default command runs the webhook
# listener so that `docker compose up app` (used to give the listener the
# stable `connector` network alias) starts serving without extra arguments.
CMD ["python", "-m", "talentforge_hooks", "serve"]
