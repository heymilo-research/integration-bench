FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
ENV DESK_BOARD_FILE=/app/input/desk_board.csv
RUN mkdir -p /app/output

CMD ["python", "-m", "placemint_board_census", "run-census"]
