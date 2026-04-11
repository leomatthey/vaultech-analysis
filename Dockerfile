# --- Stage: base image + uv ---
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# --- Dependencies (cached layer) ---
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# --- Runtime files only ---
COPY app/ app/
COPY src/ src/
COPY models/ models/
COPY data/gold/ data/gold/

# Install the project itself (vaultech_analysis package)
RUN uv sync --frozen --no-dev

# --- Run ---
EXPOSE 8501
CMD ["uv", "run", "streamlit", "run", "app/streamlit_app.py", \
     "--server.headless", "true", \
     "--server.port", "8501", \
     "--server.address", "0.0.0.0"]
