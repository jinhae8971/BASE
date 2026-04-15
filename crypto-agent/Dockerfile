# Multi-stage Dockerfile for crypto-agent.
#
# Stage 1 (builder): install dependencies into a fresh venv.
# Stage 2 (runtime): copy the venv + source into a distroless-ish slim
#   image so the final layer contains only what the scheduler needs.
#
# Build:
#   docker build -t crypto-agent:latest .
#
# Run (dry mode smoke test — never places real orders):
#   docker run --rm -e TRADING_MODE=dry crypto-agent:latest once --dry-run
#
# Run (scheduler, paper mode):
#   docker run -d --name crypto-agent \
#     -e TRADING_MODE=paper \
#     -e BINANCE_API_KEY=... -e BINANCE_API_SECRET=... \
#     -e ANTHROPIC_API_KEY=... \
#     -v $PWD/data:/app/data \
#     crypto-agent:latest schedule --interval-hours 24

ARG PYTHON_VERSION=3.12-slim

# -----------------------------------------------------------------------------
# Stage 1: builder
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# System deps for building any C extensions pydantic-core etc. may need.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
 && rm -rf /var/lib/apt/lists/*

# Create a dedicated venv so stage 2 can copy it wholesale.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip \
 && pip install -e .

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    TRADING_MODE=dry

# Run as an unprivileged user so the process can't write outside /app/data.
RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app src ./src
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app pyproject.toml README.md ./

# Persisted state (run artifacts, NDJSON archives) lives here.
RUN mkdir -p /app/data && chown -R app:app /app/data
VOLUME ["/app/data"]

USER app

# Default: run the scheduler in foreground so container logs carry the output.
ENTRYPOINT ["python", "-m", "src.orchestrator.run_daily"]
CMD ["once", "--dry-run"]
