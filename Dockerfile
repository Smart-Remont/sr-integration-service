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
# wkhtmltopdf: the provider tables of /api/mailing, as in PHP. Debian 13 no longer has the package, so it
# comes from the releases of the project. It needs no X server: this build has its own patched Qt.
ADD --checksum=sha256:98ba0d157b50d36f23bd0dedf4c0aa28c7b0c50fcdcdc54aa5b6bbba81a3941d \
    https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.bookworm_amd64.deb \
    /tmp/wkhtmltox.deb

RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 /tmp/wkhtmltox.deb \
    && rm -rf /var/lib/apt/lists/* /tmp/wkhtmltox.deb

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
