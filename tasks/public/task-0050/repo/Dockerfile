FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
ENV CORRECTIONS_FILE=/app/input/corrections.csv
RUN mkdir -p /app/output

CMD ["python", "-m", "placemint_fee_corrections", "apply-corrections"]
