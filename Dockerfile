# Aarkib Media Server Dockerfile
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

# Enable bytecode compilation and unbuffered logging
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    AARKIB_DATA_DIR=/app/data

# Cache dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source tree and files
COPY src/ ./src/
COPY README.md LICENSE ./
RUN uv sync --frozen --no-dev

# Install system runtime dependencies with VAAPI hardware acceleration (Intel & AMD)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        vainfo \
        libva2 \
        libva-drm2 \
        i965-va-driver \
        intel-media-va-driver \
        mesa-va-drivers && \
    rm -rf /var/lib/apt/lists/*

# Create unprivileged user with hardware device access and default data directories
RUN (getent group render || groupadd -r render || true) && \
    (getent group video || groupadd -r video || true) && \
    groupadd -g 1000 aarkib && \
    useradd -u 1000 -g aarkib -G video,render -d /app -s /bin/sh aarkib && \
    mkdir -p /app/data/media /app/data/books /app/data/covers /app/data/optimized /app/data/transcode /app/data/podcasts && \
    chown -R aarkib:aarkib /app

# Expose default HTTP port
EXPOSE 5000

# Container healthcheck using standard library urllib
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/health')" || exit 1

# Run as non-root user
USER aarkib

# Run Aarkib server
CMD [".venv/bin/aarkib"]
