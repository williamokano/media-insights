# syntax=docker/dockerfile:1.7
# ---------- builder ----------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:0.7.13 /uv /uvx /usr/local/bin/

WORKDIR /build

# Install into a relocatable venv that we copy into the runtime image.
ENV UV_PROJECT_ENVIRONMENT=/install
COPY pyproject.toml README.md ./
COPY src ./src

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-editable && \
    uv export --no-dev --no-emit-project -o /tmp/requirements.txt

# ---------- runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/install \
    PATH="/install/bin:${PATH}"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg ca-certificates tini gosu && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 media && \
    useradd -u 1000 -g media -d /config -s /sbin/nologin media

WORKDIR /app
COPY --from=builder /install /install
COPY --from=builder /build/src /app/src
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

VOLUME ["/config", "/data"]
EXPOSE 8765

# Runs as root only long enough for entrypoint.sh to remap media to
# PUID/PGID and drop privileges with gosu (arr-stack convention).
ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["media-insights", "--config", "/config/config.yaml", "serve", "--host", "0.0.0.0", "--port", "8765"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, sys; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3).read(); sys.exit(0)" || exit 1