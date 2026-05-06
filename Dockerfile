# ============================================================
# MAIS — Multi-Agent Investment System
# Python 3.11 + all dependencies
# ============================================================
FROM python:3.11-slim

WORKDIR /app

# System dependencies (gcc/g++ needed for scipy/cvxpy wheels on ARM)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        git \
        curl \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# Timezone: Asia/Seoul
ENV TZ=Asia/Seoul
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# ── Python dependencies ──────────────────────────────────────
# Copy only the build spec first so Docker can cache this layer
COPY pyproject.toml ./

# Install package in editable mode including dashboard extras
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir ".[dev,dashboard]"

# ── Pre-cache ChromaDB ONNX embedding model ──────────────────
# This downloads ~60 MB from HuggingFace once at build time so
# the container starts instantly without network dependency.
RUN python -c "\
try:\
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2;\
    fn = ONNXMiniLM_L6_V2();\
    fn(['warmup']);\
    print('ChromaDB ONNX model cached OK')\
except Exception as e:\
    print(f'ChromaDB model cache skipped: {e}')\
" || true

# ── Source code ──────────────────────────────────────────────
COPY . .

# Create runtime directories (overridden by volume mounts in compose)
RUN mkdir -p data_store/reflections data_store/chroma logs

# ── Environment ───────────────────────────────────────────────
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Streamlit port (only relevant for the dashboard service)
EXPOSE 8501

# Default: run the scheduler (override in compose per service)
CMD ["python", "-m", "scheduler.scheduler_service"]
