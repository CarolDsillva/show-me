# Sandbox image: linkding at a pinned commit, plus Chromium through Playwright.
# Holds no secrets. The running container has no network, so everything it needs
# has to be here. Base images are pinned by digest.

FROM node:22-bookworm-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da AS frontend
ARG LINKDING_SHA=27b7303baf41bb28babc610ac8eaa486e1ddfab5
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && mkdir /src \
    && curl -fsSL "https://github.com/sissbruecker/linkding/archive/${LINKDING_SHA}.tar.gz" \
       | tar -xz --strip-components=1 -C /src
WORKDIR /src
RUN npm ci && npm run build && rm -rf node_modules

FROM python:3.13-slim-bookworm@sha256:281d39ff02e3b389401586134ff9e6572954687822a58055ec07a60ded4e0b7f AS build
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    DJANGO_SETTINGS_MODULE=bookmarks.settings.dev \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

COPY --from=frontend /src /opt/linkding
WORKDIR /opt/linkding

# uwsgi is only used in production and needs a compiler, so it's left out.
RUN pip install --no-cache-dir uv==0.13.7 \
    && uv sync --frozen --no-install-package uwsgi \
    && playwright install --with-deps chromium \
    && mkdir -p data/favicons data/previews data/assets \
    && python manage.py migrate --no-input \
    && chmod -R a+rX /opt \
    && rm -rf /root/.cache /var/lib/apt/lists/*

# Flatten, so the final image carries only the ENV declared here. Base images add their
# own (python adds GPG_KEY, a public fingerprint), and Docker can't unset an inherited
# name. Attestation rejects credential-shaped names, and that check stays strict.
FROM scratch
COPY --from=build / /
ENV LANG=C.UTF-8 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    DJANGO_SETTINGS_MODULE=bookmarks.settings.dev \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /opt/linkding
USER 65534:65534
