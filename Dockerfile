# syntax=docker/dockerfile:1.6
# Multi-stage Dockerfile for the Multi-Agent Investment System (MAIS)
#
# Build:
#     docker build -t mais:latest .
#
# Targets:
#     scheduler  — APScheduler long-running daily/weekly jobs (default)
#     dashboard  — Streamlit dashboard on port 8501
#     daily      — one-shot daily pipeline (paper, dry-run)
#     backtest   — one-shot backtest

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    TZ=Asia/Seoul \
    MAIS_TZ=Asia/Seoul

# System packages — cvxpy + chromadb need build-essential & libgomp.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        git \
        curl \
        ca-certificates \
        tzdata \
        libgomp1 \
        libstdc++6 \
    && ln -sf /usr/share/zoneinfo/Asia/Seoul /etc/localtime \
    && echo "Asia/Seoul" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (better layer caching).
COPY pyproject.toml ./
RUN pip install --upgrade pip setuptools wheel \
    && pip install \
        "anthropic>=0.39.0" \
        "pydantic>=2.6" "pydantic-settings>=2.2" \
        "pyyaml>=6.0" "python-dotenv>=1.0" \
        "httpx>=0.27" "tenacity>=8.2" \
        "apscheduler>=3.10" "pytz>=2024.1" \
        "pandas>=2.2" "numpy>=1.26" "scipy>=1.12" \
        "rich>=13.7" "typer>=0.12" "structlog>=24.1" \
        "sqlalchemy>=2.0" \
        "feedparser>=6.0" \
        "prometheus-client>=0.20" \
        "yfinance>=0.2" \
        "FinanceDataReader>=0.9.90" \
        "pykrx>=1.0.45" \
        "OpenDartReader>=0.2.2" \
        "PyPortfolioOpt>=1.5" \
        "cvxpy>=1.4" \
        "chromadb>=0.4.24" \
        "streamlit>=1.33" "plotly>=5.20"

COPY . .

# Default data directory mounted by docker-compose
VOLUME ["/app/data_store"]

# Default command: scheduler. Compose overrides this per service.
CMD ["python", "-m", "scheduler.scheduler"]
