# CaptN-BRAIN — containerized runtime
#
# Two layers of isolation:
#   1. The Docker container itself (outer sandbox): non-root user, no-new-privileges,
#      secrets mounted read-only, host LLM reached only via host.docker.internal.
#   2. wd-40/run.py (inner sandbox): for running UNTRUSTED scripts inside the
#      container with writes/spawns/secrets/network gated by an audit hook.
#
# Secrets (.captn/) are NOT copied here — see .dockerignore and the compose file.
FROM python:3.11-slim

# Non-root runtime user
RUN groupadd -r app && useradd -r -g app app

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps first so this layer is cached across code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the project source (secrets excluded by .dockerignore)
COPY . .

# wd-40 needs these dirs present at runtime (they are volumes in compose)
RUN mkdir -p wd-40/sandbox wd-40/logs wd-40/feeds wd-40/quarantine \
    && chown -R app:app wd-40

# Drop privileges
USER app

# Default entrypoint: the Streamlit dashboard.
# Override at runtime, e.g. to run an untrusted script through the sandbox:
#   docker compose run --rm captn python wd-40/run.py scripts/foo.py
#   docker compose run --rm captn python wd-40/run.py --net captn/workers/crawler.py
EXPOSE 8501
ENTRYPOINT ["python", "-m", "streamlit", "run", "captn/dashboard/app.py", \
            "--server.port=8501", "--server.address=0.0.0.0"]
