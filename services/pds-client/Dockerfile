FROM python:3.12-slim AS base

FROM base AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM base AS runtime
RUN groupadd --system app && useradd --system --gid app --uid 10001 app
WORKDIR /app
COPY --from=builder /install /usr/local
COPY --chown=app:app main.py .
USER 10001

HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8002/healthz').status==200 else 1)"

EXPOSE 8002
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
