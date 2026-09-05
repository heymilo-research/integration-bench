FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
ENV STATE_DIR=/app/state
ENV SNAPSHOT_FILE=/app/input/warehouse_mirror.json
RUN mkdir -p /app/output /app/state && chmod 0777 /app/state

CMD ["python", "-m", "harbour_parity_probe", "serve"]
