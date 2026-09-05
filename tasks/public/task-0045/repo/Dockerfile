FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
RUN mkdir -p /app/output

CMD ["python", "-m", "staffline_to_bullpen_migrate"]
