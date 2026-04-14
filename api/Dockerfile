FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Dependencies (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application code and data
COPY src/ src/
COPY reference_times.json .

EXPOSE 80
CMD ["uv", "run", "uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "80"]
