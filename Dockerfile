# =============================================================================
# Stage 1: Build virtual environment with uv
# Base image pinned to multi-arch manifest list digest (linux/amd64 and linux/arm64)
# To update: inspect latest digest on ghcr.io/astral-sh/uv:python3.14-bookworm-slim
# =============================================================================
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim@sha256:7cf77f594be8042dab6daa9fe326f90962252268b4f120a7f5dccce4d947e6c1 AS builder

WORKDIR /app

# Enable bytecode compilation and unbuffered logging
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# 1. Install dependencies first with BuildKit cache for optimal Docker layer caching
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync \
        --frozen \
        --no-dev \
        --no-install-project \
        --no-editable

# 2. Copy application source and build non-editable production wheel
COPY src/ ./src/
COPY README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync \
        --frozen \
        --no-dev \
        --no-editable


# =============================================================================
# Stage 2: Hardened minimal runtime environment
# Base image pinned to multi-arch manifest list digest (linux/amd64 and linux/arm64)
# To update: inspect latest digest on library/python:3.14-slim-bookworm
# =============================================================================
FROM python:3.14-slim-bookworm@sha256:9ab8d9c8514b44f90cf0029dd42fdd7e9e211e639c8b995304cc04568dee900f AS runtime

WORKDIR /app

# Runtime environment configuration
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    AARKIB_DATA_DIR=/app/data

# Install system runtime dependencies with Intel & AMD VAAPI hardware acceleration drivers
# Diagnostic packages (such as vainfo) and compilers are excluded to minimize attack surface
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        libva2 \
        libva-drm2 \
        i965-va-driver \
        intel-media-va-driver \
        mesa-va-drivers && \
    rm -rf /var/lib/apt/lists/*

# Create unprivileged user with hardware groups, ensure data directories exist
RUN (getent group render || groupadd -r render || true) && \
    (getent group video || groupadd -r video || true) && \
    groupadd -g 1000 aarkib && \
    useradd -u 1000 -g aarkib -G video,render -d /app -s /bin/sh aarkib && \
    mkdir -p /app/data/media /app/data/covers /app/data/optimized /app/data/transcode /app/data/backups && \
    chown -R aarkib:aarkib /app/data

# Copy isolated virtual environment from builder stage (root-owned for immutability)
COPY --from=builder /app/.venv /app/.venv

# Expose default HTTP port
EXPOSE 5000

# Container healthcheck using standard library urllib against the authenticated-exempt health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/health')" || exit 1

# Run as non-root user
USER aarkib

# Run Aarkib production server
CMD ["aarkib"]
