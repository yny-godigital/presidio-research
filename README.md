# PII Detection Eval

Presidio-based PII detection service (`presidio-service/`, Python FastAPI)
plus a reusable eval harness (`eval/`, TypeScript) that scores detection and
token-restoration quality against labeled PDFs.

## Layout

- `presidio-service/` — detection API. `POST /analyze` takes `{text,
  language}` and returns entity spans; recognizers come from
  `recognizers.yaml` (Presidio registry format) plus the Aho-Corasick
  company gazetteer. `POST /anonymize` returns the anonymized text per
  `policy/policy.yaml` (`mode: "reversible"` also returns the
  token → original mapping) and `POST /restore` reverts that text with the
  mapping. Entry point: `app.py`.
- `eval/` — labeling and scoring tools. See `eval/README.md`.
- `demo/inputs/` — PDFs plus `labels/` ground truth and `text/` page dumps.
- `policy/policy.yaml` — entity → action map used by the roundtrip scorer.

## Quickstart

Requirements: Node ≥ 20, `uv`.

```bash
cd presidio-service && uv sync
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 3000
```

```bash
npm install
npm test && npm run typecheck
npm run eval -- --only <labeled-stem>
```

## Environment variables (service)

| Variable | Meaning | Default |
| --- | --- | --- |
| `RECOGNIZER_REGISTRY_CONF_FILE` | Registry YAML path (all patterns, lists, scores, enables) | `presidio-service/recognizers.yaml` |
| `POLICY_CONF_FILE` | Entity → action policy for `POST /anonymize` | `policy/policy.yaml` |
| `PRESIDIO_ANALYZER_URL` | Analyzer URL for the eval tools | `http://127.0.0.1:3000` |
