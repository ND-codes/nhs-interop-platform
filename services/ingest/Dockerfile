FROM python:3.12-slim AS base

# --- builder layer -----------------------------------------------------------
FROM base AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- runtime layer -----------------------------------------------------------
FROM base AS runtime
# Non-root user for the workload (matches runAsNonRoot: true in the Helm chart)
RUN groupadd --system app && useradd --system --gid app --uid 10001 app
WORKDIR /app
COPY --from=builder /install /usr/local
COPY --chown=app:app main.py .
USER 10001

# Health probe used by docker-compose; Kubernetes uses the HTTP probes on /healthz
HEALTHCHECK --interval=30s --timeout=10s --retries=60 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
