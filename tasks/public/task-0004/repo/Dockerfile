FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY tests ./tests

ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
RUN mkdir -p /app/output

CMD ["python", "-m", "staffline_placemint_merge"]
