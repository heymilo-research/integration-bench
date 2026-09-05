FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
ENV REDEPLOYMENTS_FILE=/app/input/redeployments.csv
RUN mkdir -p /app/output

CMD ["python", "-m", "placemint_movement_sync", "apply-movements"]
