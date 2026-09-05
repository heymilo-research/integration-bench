FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
ENV STATE_DIR=/app/state
ENV CROSSWALK_FILE=/app/input/placement_links.csv
RUN mkdir -p /app/output /app/state

CMD ["python", "-m", "northgate_placement_sync"]
