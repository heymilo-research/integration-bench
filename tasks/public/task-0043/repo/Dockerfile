FROM python:3.12-slim

WORKDIR /app

COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
ENV STATE_DIR=/app/state
RUN mkdir -p /app/output /app/state && chmod 0777 /app/state

CMD ["python", "-m", "marchfield_rota_sync"]
