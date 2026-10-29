# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

# Production image. See Dockerfile.demo for the public-demo variant, which builds on top
# of this image rather than duplicating it (see that file). Runs with
# OPENCLEAR_ENVIRONMENT=production baked in (config.py::assert_production_safe) -- a
# deployment that forgets to supply real signing keys / OPENCLEAR_SSO_ENCRYPTION_KEY / WSUD
# text refuses to start with a clear error instead of silently serving with the
# checked-in dev/test defaults every clone of this repo shares. See README.md's "Docker"
# section for the full list of what must be supplied at deploy time, and its "Reverse
# proxy / WAF deployment" section for why this must stay a single instance -- web/
# rate_limit.py's per-IP limiter and services/demo_tenant_service.py's idle-reset
# tracking are both in-process state, not shared across replicas.

FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS builder

# gcc/libpq-dev: build-time deps for compiling the [postgres] extra's psycopg wheel, if
# requested via the EXTRAS build arg below -- not needed at runtime, so this whole stage
# is discarded and never ships in the final image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
COPY requirements/ requirements/
COPY src/ src/
COPY migrations/ migrations/
COPY alembic.ini .

# Dependencies come only from the hashed lock (OWASP_PLAN.md OW-D9; scripts/lock.sh): every
# runtime extra that needs no system libraries (PDF export, Postgres, S3, Azure, Redis, SMS,
# cloud OCR), so one image works everywhere, and nothing is installed that the lock didn't
# name and hash. EXTRAS adds what needs system libraries, unlocked, e.g.
# --build-arg EXTRAS=mssql (with unixODBC) or EXTRAS=opencv.
ARG EXTRAS=
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"
RUN pip install --no-cache-dir --require-hashes -r requirements/runtime.txt \
    && pip install --no-cache-dir --no-deps -e . \
    && if [ -n "${EXTRAS}" ]; then pip install --no-cache-dir -e ".[${EXTRAS}]"; fi


FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f

# tesseract-ocr: the system binary pytesseract shells out to (ocr/tesseract_provider.py).
# libpango/libcairo/libgdk-pixbuf: weasyprint's runtime deps for PDF export --
# doc_pdf_service.py::weasyprint_usable() soft-warns and just disables the "Download PDF"
# button if these are missing rather than crashing, but a production image should have
# them. libpq5: psycopg's runtime (not build-time) dependency, needed only if
# OPENCLEAR_DATABASE_URL is pointed at Postgres -- harmless/unused with the default SQLite.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 openclear
WORKDIR /app
COPY --from=builder /venv /venv
COPY --from=builder /app/pyproject.toml /app/pyproject.toml
COPY src/ src/
COPY migrations/ migrations/
COPY alembic.ini .
# The license page (web/routers/license.py) serves these: OpenClear's license, and the third-party
# notices its components' licenses ask to travel with it (plans/THIRD_PARTY_PLAN.md TP-D3).
COPY LICENSE THIRD_PARTY_NOTICES.md ./
# dev_keys/ is already public (checked into this repo's git history) -- copying it in
# doesn't newly expose anything, and it's what lets `docker compose up` (see
# docker-compose.yml, a local Postgres-testing convenience, not this image's normal
# deploy path) run in development mode with zero key setup, same as the local launcher.
# assert_production_safe is what actually prevents it from being used for real once
# OPENCLEAR_ENVIRONMENT=production is in effect (the default below) -- it compares the
# *configured path string* against the checked-in default, independent of whether these
# files happen to exist in the image.
COPY dev_keys/ dev_keys/
# Screenshots the in-app End User/Admin Documentation pages embed and link to (see
# main.py's /static/docs-screenshots mount and services/doc_pdf_service.py) -- without
# these, those pages render with broken images, not a missing feature exactly, but a
# visibly broken one.
COPY docs/screenshots/ docs/screenshots/

ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    OPENCLEAR_ENVIRONMENT=production \
    OPENCLEAR_DATABASE_URL=sqlite:////data/pospay.db \
    OPENCLEAR_CHECK_IMAGE_STORAGE_DIR=/data/check_images \
    OPENCLEAR_TENANT_ASSET_STORAGE_DIR=/data/tenant_assets \
    OPENCLEAR_BULK_UPLOAD_STORAGE_DIR=/data/bulk_uploads \
    OPENCLEAR_ML_ARTIFACT_DIR=/data/ml_artifacts \
    OPENCLEAR_DATA_EXPORT_STORAGE_DIR=/data/exports \
    OPENCLEAR_DECISION_FILE_STORAGE_DIR=/data/decision_files \
    OPENCLEAR_AUDIT_ARCHIVE_STORAGE_DIR=/data/audit_archives \
    OPENCLEAR_AUTO_IMPORT_DROPBOX_DIR=/data/auto_import_dropbox

# Deliberately NOT setting OPENCLEAR_*_KEY_PATH/OPENCLEAR_SSO_ENCRYPTION_KEY/OPENCLEAR_WSUD_*_TEXT
# here to anything other than their class defaults (dev_keys/...) -- doing so would
# quietly satisfy assert_production_safe's "still at the checked-in default" check
# without the operator having actually supplied anything real. Those stay real secrets
# supplied at deploy time (env vars, a mounted secrets volume) -- see README.md.

RUN mkdir -p /data && chown -R openclear:openclear /data /app /venv
VOLUME ["/data"]
USER openclear

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

# Keep-alive longer than the idle timeout of whatever sits in front (a load balancer or edge
# network reuses idle connections; if the app closes one first, users see sporadic 502/520
# errors). 75 seconds suits most load balancers; raise OPENCLEAR_KEEPALIVE_SECONDS to match
# your edge's origin idle timeout (WAF_PLAN.md W3).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn openclear.main:app --host 0.0.0.0 --port 8000 --timeout-keep-alive ${OPENCLEAR_KEEPALIVE_SECONDS:-75}"]
