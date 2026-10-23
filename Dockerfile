FROM python:3.14-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_PROJECT_ENVIRONMENT="/usr/local/"

COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /bin/

WORKDIR /app

COPY ./pyproject.toml ./uv.lock ./

RUN uv sync --frozen --no-dev --no-cache --no-install-project

COPY ./app .

# KL recognition runs Composer through the Cursor SDK, which is a Node.js package.
FROM node:22.23.4-trixie-slim AS composer_sdk

WORKDIR /sdk

COPY ./app/src/features/kl_recognition/composer_sdk/package.json \
     ./app/src/features/kl_recognition/composer_sdk/package-lock.json ./

RUN npm ci --omit=dev --no-audit --no-fund

FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# OpenCV (KL plan cropping) needs these at runtime even in the headless build.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --no-create-home --uid 10001 app

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.14 /usr/local/lib/python3.14
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder --chown=app:app /app /app
COPY --from=composer_sdk /usr/local/bin/node /usr/local/bin/node
COPY --from=composer_sdk --chown=app:app /sdk/node_modules \
     /app/src/features/kl_recognition/composer_sdk/node_modules

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

CMD ["python", "run_main.py"]
