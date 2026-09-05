FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
RUN mkdir -p /app/output

# The task's scenarios invoke this container with an explicit subcommand
# (sync / dump). The default command runs one polling pass so a plain
# `docker compose up app` does something sensible.
CMD ["python", "-m", "globalhire_sync", "sync"]
