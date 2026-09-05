FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY tests ./tests

ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
RUN mkdir -p /app/output

# Default one-shot command for the corrections surface.
CMD ["python", "-m", "hirewire_corrections", "correct"]
