FROM python:3.12-slim

WORKDIR /app

COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
RUN mkdir -p /app/output

CMD ["python", "-m", "nordhavn_mirror_port"]
