# Justfile for pii-redaction — unified command runner.
# Every recipe is a thin wrapper over existing npm/docker/uv/scripts.
# Run `just` (default) to list all recipes grouped by area.

set dotenv-load := false

# List all recipes grouped by area.
default:
    @just --list --unsorted

# ---- setup ----

# Full first-time setup: env file, node deps, python service deps.
[group('setup')]
setup: setup-env install presidio-sync

# Copy .env.example to .env when missing; never overwrite.
[group('setup')]
setup-env:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -f .env ]; then echo ".env already exists, leaving it alone."; else cp .env.example .env && echo "Created .env from .env.example."; fi

# Install Node dependencies.
[group('setup')]
install:
    npm install

# Fail fast when required analyzer variables or policy file are missing.
[group('setup')]
env-check:
    #!/usr/bin/env bash
    set -euo pipefail
    set -a; [ -f .env ] && source .env; set +a
    missing=""
    for v in PRESIDIO_ANALYZER_URL POLICY_FILE; do
      if [ -z "${!v:-}" ]; then missing="$missing $v"; fi
    done
    if [ -n "$missing" ]; then echo "Missing required env vars:$missing" >&2; exit 1; fi
    if [ ! -f "${POLICY_FILE:-policy/policy.yaml}" ]; then echo "Missing policy file: ${POLICY_FILE:-policy/policy.yaml}" >&2; exit 1; fi
    echo "Env check passed."

# ---- presidio ----

# Sync the Python detector dependencies.
[group('presidio')]
presidio-sync:
    cd presidio-service && uv sync

# ---- quality ----

# Run the container-free vitest suite.
[group('quality')]
test:
    npm test

# Typecheck the TypeScript workspace.
[group('quality')]
typecheck:
    npm run typecheck

# Pre-push gate: typecheck plus Node and Python tests.
[group('quality')]
check: typecheck test presidio-test

# Run the Python detector test suite.
[group('presidio')]
presidio-test:
    cd presidio-service && uv run pytest -q

# Serve the Python detector locally (matches compose host port).
[group('presidio')]
presidio-serve port="3000":
    cd presidio-service && uv run uvicorn app:app --port {{port}}

# ---- compose ----

# Start compose services detached (default: presidio-analyzer).
[group('compose')]
compose-up services="presidio-analyzer":
    docker compose up -d {{services}}

# Tear down compose services.
[group('compose')]
compose-down:
    docker compose down

# Follow logs for a compose service.
[group('compose')]
compose-logs service="presidio-analyzer":
    docker compose logs -f {{service}}

# Wait for the Presidio analyzer /health to report up (60 tries, 2s apart).
[group('compose')]
wait-presidio url="http://127.0.0.1:3000/health":
    #!/usr/bin/env bash
    set -euo pipefail
    for i in $(seq 1 60); do
      if curl -sf "{{url}}" > /dev/null; then echo "Presidio analyzer is healthy."; exit 0; fi
      sleep 2
    done
    echo "Presidio analyzer never became healthy at {{url}}" >&2; exit 1

# ---- eval ----

# Score Presidio detections against labels; results go to eval/results/summary.json.
[group('eval')]
eval analyzer="" only="":
    #!/usr/bin/env bash
    set -euo pipefail
    set -a; [ -f .env ] && source .env; set +a
    a="{{analyzer}}"
    if [ -z "$a" ]; then a="${PRESIDIO_ANALYZER_URL:-http://127.0.0.1:3000}"; fi
    args=(--analyzer "$a")
    if [ -n "{{only}}" ]; then args+=(--only "{{only}}"); fi
    npm run eval -- "${args[@]}"

# Score only the given comma-separated label stems (freshly labeled pilots).
[group('eval')]
eval-only stems:
    just eval "" "{{stems}}"

# Generate candidate ground-truth labels for human review (never --force).
[group('eval')]
prelabel only="" min-score="0.35":
    #!/usr/bin/env bash
    set -euo pipefail
    set -a; [ -f .env ] && source .env; set +a
    a="${PRESIDIO_ANALYZER_URL:-http://127.0.0.1:3000}"
    args=(--analyzer "$a")
    if [ -n "{{only}}" ]; then args+=(--only "{{only}}"); fi
    npx tsx eval/prelabel.ts "${args[@]}" --min-score {{min-score}}

# Turn human value lists into label offsets.
[group('eval')]
resolve-labels only="" force="":
    #!/usr/bin/env bash
    set -euo pipefail
    args=()
    if [ -n "{{only}}" ]; then args+=(--only "{{only}}"); fi
    if [ -n "{{force}}" ]; then args+=(--force); fi
    npx tsx eval/resolve-labels.ts "${args[@]}"

# ---- demo ----

# Full analyzer demo: compose up, wait healthy, then score the full corpus.
[group('demo')]
demo: compose-up wait-presidio
    just eval

# Quick manual check: one PII text through the analyzer, pretty-printed.
[group('demo')]
curl-example:
    #!/usr/bin/env bash
    set -euo pipefail
    set -a; [ -f .env ] && source .env; set +a
    a="${PRESIDIO_ANALYZER_URL:-http://127.0.0.1:3000}"
    curl -s "$a/analyze" \
      -H "Content-Type: application/json" \
      -d '{"text":"Email John Tan at john.tan@example.com about ABC Engineering Sdn Bhd project PRJ-2026-001.","language":"en"}' | jq .
