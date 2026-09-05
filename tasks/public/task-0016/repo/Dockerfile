FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
ENV INPUT_FILE=/app/input/pending_events.json
RUN mkdir -p /app/output

# The task's scenarios invoke this container with an explicit subcommand
# (push / poll / dump) via `docker compose run app <cmd>`. A bare `run` with no
# args prints usage; there is no long-lived server for this surface.
CMD ["python", "-m", "hirewire_connector", "push"]
