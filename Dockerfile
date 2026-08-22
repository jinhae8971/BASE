# ---------------------------------------------------------------------------
# 업비트 알트코인 자동매매 — 운영용 컨테이너
#
#   docker compose up -d --build      기동
#   docker compose logs -f            로그
#
# 대시보드와 스케줄러가 한 프로세스로 돌아가므로, 이 컨테이너만 살아 있으면
# 매일 09:10 KST 종목 선정과 5분 간격 청산 점검이 자동으로 수행된다.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Seoul \
    UPBIT_DASHBOARD_HOST=0.0.0.0 \
    UPBIT_DASHBOARD_PORT=8787

# tzdata so container logs and `date` read in KST — the engine itself already
# pins Asia/Seoul explicitly, this is for the operator's benefit.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer first: source edits below must not re-resolve pip.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install -e ".[upbit]"

COPY config/ ./config/
COPY scripts/ ./scripts/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# Non-root. data_store/ is chowned here so a *named volume* inherits the
# ownership when Docker seeds it from the image.
RUN chmod +x /usr/local/bin/entrypoint.sh \
 && useradd --create-home --uid 10001 --shell /usr/sbin/nologin upbit \
 && mkdir -p /app/data_store \
 && chown -R upbit:upbit /app

USER upbit

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=5).status == 200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["serve"]
