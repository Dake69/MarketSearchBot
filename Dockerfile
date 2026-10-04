FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app
COPY pyproject.toml ./
RUN pip install \
      "aiosqlite>=0.20,<1" \
      "httpx>=0.27,<1" \
      "playwright>=1.48,<2" \
      "psycopg[binary]>=3.2,<4" \
      "python-dotenv>=1.0,<2" \
      "PyYAML>=6.0,<7" \
    && playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

COPY README.md ./
COPY app ./app
RUN pip install --no-deps .

COPY searches.example.yaml .env.example ./
RUN mkdir -p /app/data && chmod 1777 /app/data

CMD ["python", "-m", "app"]
