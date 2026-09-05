FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app/src

ENV OUTPUT_DIR=/app/output
ENV ACCOUNT_BOOK_FILE=/app/input/account_book.json
RUN mkdir -p /app/output

CMD ["python", "-m", "placemint_book_extract", "extract"]
