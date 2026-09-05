FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
ENV INPUT_DIR=/app/input
RUN mkdir -p /app/output

CMD ["python", "-m", "vaultd_cutover", "sync"]
