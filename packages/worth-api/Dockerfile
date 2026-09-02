# worth-api. Built from the repository root so the workspace packages are in
# context:  docker build -f packages/worth-api/Dockerfile .
#
# The pinned CMS archives are fetched and hash-verified at build time, so the
# running container needs no network to hold the full fee schedule. Build with
# --build-arg CMS_ARCHIVES=0 to skip them; the server then falls back to the
# committed eight-code fixture and says so in /api/health.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_FROZEN=1 \
    PYTHONUNBUFFERED=1 \
    WORTH_CACHE_DIR=/app/cms

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
RUN uv sync --all-packages --no-dev

ARG CMS_ARCHIVES=1
RUN if [ "$CMS_ARCHIVES" = "1" ]; then \
      uv run --no-sync python -c "from worth_fees.sources import PINNED_VINTAGES, fetch_archive; [fetch_archive(v) for v in PINNED_VINTAGES.values()]" \
      && ls -la /app/cms; \
    fi

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "worth_api.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
