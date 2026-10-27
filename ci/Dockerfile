# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
#
# The image CI's tests run in (plans/TEST_SPEED_PLAN.md TS-D8): everything the suite needs from the
# operating system, installed once here, so no CI job runs apt (a slow Ubuntu mirror used to add
# minutes to a random job). Built by .github/workflows/ci-image.yml for each Python version CI
# tests; tests.yml pins it by digest. Python packages still come from the hashed lock in each job.
#
#   docker build --build-arg PYTHON_IMAGE=python:3.13-slim-bookworm@sha256:... \
#     --build-arg PLAYWRIGHT_VERSION=1.64.1 -t openclear-ci ci/

ARG PYTHON_IMAGE
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d

FROM ${NODE_IMAGE} AS node

FROM ${PYTHON_IMAGE}
ARG PLAYWRIGHT_VERSION

# tesseract-ocr: OCR. libpango, libpangoft2, libcairo2: WeasyPrint's PDFs. git: actions/checkout.
# libstdc++6: Node. The TypeScript SDK's tests run TypeScript directly, which needs Node 22.18+.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr libpango-1.0-0 libpangoft2-1.0-0 libcairo2 \
       git ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node

# Playwright's headless Chromium (the tests never open a window) and its system libraries, at the
# version the lock pins. When the lock moves on, the test job downloads the matching Chromium (no
# system libraries needed) until the next build.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir "playwright==${PLAYWRIGHT_VERSION}" \
    && python -m playwright install --with-deps --only-shell chromium \
    && pip uninstall -y playwright \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /ms-playwright

LABEL org.opencontainers.image.source="https://github.com/chaffed/OpenClear" \
      org.opencontainers.image.description="The image OpenClear's CI tests run in" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"
