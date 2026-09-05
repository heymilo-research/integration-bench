FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
COPY input /app/input

ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
ENV PYTHONUNBUFFERED=1

# The harness runs candidate code as the invoking non-root uid. Docker copies
# this directory's mode into the task-scoped app-state volume on first use.
RUN mkdir -p /app/state && chmod 0777 /app/state

CMD ["python", "-m", "placemint_summit", "serve"]
