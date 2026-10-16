FROM python:3.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY presidio-service/pyproject.toml presidio-service/uv.lock presidio-service/.python-version ./
COPY presidio-service/recognizers.yaml presidio-service/gazetteer.yaml ./
RUN uv sync --frozen --no-dev
COPY presidio-service/app.py presidio-service/config.py presidio-service/recognizer_registry.py presidio-service/recognizers.py presidio-service/gazetteer.py presidio-service/anonymizer.py ./
# The image carries its own copy of the shared policy file; the env var
# overrides the repository-relative default, which does not exist in the image.
COPY policy/policy.yaml ./policy.yaml
ENV POLICY_CONF_FILE=/app/policy.yaml
EXPOSE 3000
CMD ["uv", "run", "--frozen", "--no-dev", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000"]
