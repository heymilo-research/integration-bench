FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
RUN mkdir -p /app/output

# The compose file passes an explicit subcommand. The default keeps the
# listener leg of the cut-over running so `docker compose up app` does
# something sensible.
CMD ["python", "-m", "tf_event_cutover", "serve"]
