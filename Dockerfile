# Buukuu Book Server Dockerfile
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

# Enable bytecode compilation and unbuffered logging
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    BUUKUU_DATA_DIR=/app/data

# Cache dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source tree and files
COPY src/ ./src/
COPY README.md LICENSE ./
RUN uv sync --frozen --no-dev

# Create default data directories
RUN mkdir -p /app/data/books /app/data/covers

# Expose default HTTP port
EXPOSE 5000

# Run Buukuu server
CMD ["uv", "run", "buukuu"]
