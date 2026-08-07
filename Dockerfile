FROM node:22-alpine AS web-build
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS application
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000
WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app
COPY pyproject.toml alembic.ini ./
COPY api/ api/
COPY domain/ domain/
COPY prompts/ prompts/
COPY migrations/ migrations/
COPY scripts/ scripts/
COPY --from=web-build /build/web/dist web/dist/
RUN python -m pip install --no-cache-dir . && mkdir -p /app/data && chown -R app:app /app

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/live', timeout=3)"
CMD ["sh", "-c", "alembic upgrade head && uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
