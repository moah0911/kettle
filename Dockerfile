FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY factory/ ./factory/
RUN uv sync --frozen --no-dev
RUN useradd -m -u 10001 kettle && chown -R kettle /app
USER kettle
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "kettle.api:app", "--host", "0.0.0.0", "--port", "8000"]
