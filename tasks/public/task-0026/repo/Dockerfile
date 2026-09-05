FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
ENV INVOICES_FILE=/app/input/invoices.csv
ENV LINES_FILE=/app/input/placement_lines.csv
RUN mkdir -p /app/output

CMD ["python", "-m", "placemint_quarter_close", "close-quarter"]
