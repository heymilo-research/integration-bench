FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
RUN mkdir -p /app/output

# The task's scenarios invoke this container with no subcommand -- a bare run
# fetches all three collections and writes all three output files. The
# default command runs the same thing so a plain `docker compose up app`
# does something sensible.
CMD ["python", "-m", "globalhire_sync"]
