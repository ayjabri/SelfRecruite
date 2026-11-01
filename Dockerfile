# SelfRecruite application image: the web page, the CLI and the crawl share this one image.
# Build:  docker compose build        Run:  docker compose up -d        (see README.md)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so code changes do not rebuild them.
COPY pyproject.toml ./
RUN python -c "import tomllib; d = tomllib.load(open('pyproject.toml', 'rb'))['project']; \
open('/tmp/requirements.txt', 'w').write('\n'.join(d['dependencies']))" \
 && pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

# The project reads config/, migrations/ and data/ relative to its own checkout, so it is
# installed in editable mode from /app rather than copied into site-packages.
COPY . .
RUN pip install --no-deps -e . && chmod +x docker/entrypoint.sh

VOLUME ["/app/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8765/healthz || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["jobagent", "ui", "--host", "0.0.0.0", "--port", "8765", "--no-reload", "--proxy-headers"]
