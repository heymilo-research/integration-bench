FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
ENV INPUT_FILE=/app/input/signups.csv
RUN mkdir -p /app/output

CMD ["python", "-m", "crewcall_signup_import", "import-signups"]
