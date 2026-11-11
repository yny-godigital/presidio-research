FROM python:3.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY presidio-service/pyproject.toml presidio-service/uv.lock presidio-service/.python-version ./
COPY presidio-service/recognizers.yaml presidio-service/gazetteer.yaml ./
RUN uv sync --frozen --no-dev
COPY presidio-service/app.py presidio-service/config.py presidio-service/recognizer_registry.py presidio-service/recognizers.py presidio-service/gazetteer.py presidio-service/anonymizer.py ./
COPY presidio-service/app.py presidio-service/config.py presidio-service/conflicts.py presidio-service/abbreviate.py presidio-service/alias_evaluator.py ./
COPY presidio-service/extraction ./extraction
# The image carries its own copy of the shared policy file; the env var
# overrides the repository-relative default, which does not exist in the image.
COPY policy/policy.yaml ./policy.yaml
ENV POLICY_CONF_FILE=/app/policy.yaml

# Configuration for container environment
# (host.docker.internal allows Docker to talk to LM Studio running on Windows)
ENV LLM_API_URL=http://host.docker.internal:1234/v1/chat/completions
ENV DEMO_TEXT_DIR=/app/demo/inputs/text
ENV EVAL_RESULTS_DIR=/app/eval/results

EXPOSE 3000
CMD ["uv", "run", "--frozen", "--no-dev", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000"]