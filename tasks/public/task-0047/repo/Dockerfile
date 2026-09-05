FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV INPUT_FILE=/app/input/mobility_actions.csv
ENV OUTPUT_DIR=/app/output
RUN mkdir -p /app/output

CMD ["python", "-m", "globalhire_mobility"]
