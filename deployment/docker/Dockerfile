# syntax=docker/dockerfile:1.7
# Backend image: FastAPI API gateway + LangGraph orchestrator + workers. Build from the project root:
#   docker build -f deployment/docker/Dockerfile -t autonomous-it-ops-copilot-backend .

FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/opt/venv/bin:$PATH" \
    APP_ENV=production LOG_JSON=true CHROMA_PERSIST_DIR=/data/chroma
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home /app --shell /usr/sbin/nologin app \
    && mkdir -p /data/chroma && chown -R app:app /data
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app backend ./backend
COPY --chown=app:app agents ./agents
COPY --chown=app:app rag ./rag
COPY --chown=app:app workflows ./workflows
COPY --chown=app:app services ./services
COPY --chown=app:app monitoring ./monitoring
COPY --chown=app:app infrastructure ./infrastructure
COPY --chown=app:app knowledge_base ./knowledge_base
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app alembic.ini ./alembic.ini
COPY --chown=app:app deployment/docker/entrypoint.sh /entrypoint.sh
RUN chmod 0555 /entrypoint.sh
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3).status == 200 else 1)"
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
