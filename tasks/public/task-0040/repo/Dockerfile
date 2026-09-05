FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
ENV INPUT_FILE=/app/input/pending_reschedules.json
RUN mkdir -p /app/output

CMD ["python", "-m", "interviewly_writeback", "serve"]
