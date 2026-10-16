"""Owned Presidio detection service with the same /analyze contract as the stock image."""

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from presidio_anonymizer import AnonymizerEngine

from anonymizer import (
    TokenCollector,
    action_for,
    build_operators,
    load_policy,
    policy_file_from_env,
    restore_text,
)
from config import load_config
from conflicts import resolve_conflicts
from recognizer_registry import build_registry, registry_file_from_env

logger = logging.getLogger("presidio-service")

CONFIG = load_config()
ENGINE = None
POLICY = None
ANONYMIZER = None

MODES = ("one_way", "reversible")
OUTPUT_FORMATS = ("token", "placeholder", "mask")


def build_engine():
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": CONFIG["nlp_model"]}],
        }
    )
    registry = build_registry(registry_file_from_env(os.environ))
    return AnalyzerEngine(
        nlp_engine=provider.create_engine(),
        registry=registry,
        supported_languages=["en"],
    )


@asynccontextmanager
async def lifespan(application: FastAPI):
    global ENGINE, POLICY, ANONYMIZER
    ENGINE = build_engine()
    # Fail fast: a service without policy cannot honor redact or block.
    POLICY = load_policy(policy_file_from_env(os.environ))
    ANONYMIZER = AnonymizerEngine()
    yield


app = FastAPI(lifespan=lifespan)


def get_engine():
    if ENGINE is None:
        raise HTTPException(
            status_code=500, detail="Analyzer engine is not initialized"
        )
    return ENGINE


def get_policy():
    if POLICY is None:
        raise HTTPException(status_code=500, detail="Policy is not initialized")
    return POLICY


def get_anonymizer():
    if ANONYMIZER is None:
        raise HTTPException(
            status_code=500, detail="Anonymizer engine is not initialized"
        )
    return ANONYMIZER


@app.get("/health")
def health():
    if ENGINE is None:
        raise HTTPException(
            status_code=503, detail="Analyzer engine is not initialized"
        )
    return {"status": "up"}


@app.post("/analyze")
def analyze(body: dict, engine=Depends(get_engine)):
    text = body.get("text", "")
    language = body.get("language", "en")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(
            status_code=422, detail="Field text must be a non-empty string"
        )
    if language != "en":
        raise HTTPException(status_code=422, detail="Only language en is supported")
    started = time.perf_counter()
    try:
        results = engine.analyze(text=text, language=language)
    except Exception as exc:  # noqa: BLE001 - gateway maps any failure to its closed 502 path
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail="Analysis failed") from exc
    # AnalyzerEngine only dedupes same-type contained spans; resolve the
    # remaining cross-recognizer overlaps so one span wins before responding.
    results = resolve_conflicts(results)
    payload = [
        {
            "entity_type": item.entity_type,
            "start": item.start,
            "end": item.end,
            "score": item.score,
        }
        for item in results
    ]
    elapsed_ms = (time.perf_counter() - started) * 1000
    by_type = sorted({item["entity_type"] for item in payload})
    logger.info(
        "Analyzed %d chars in %.1fms: %s", len(text), elapsed_ms, ",".join(by_type)
    )
    return payload


@app.post("/anonymize")
def anonymize(
    body: dict,
    engine=Depends(get_engine),
    policy=Depends(get_policy),
    anonymizer=Depends(get_anonymizer),
):
    text = body.get("text", "")
    language = body.get("language", "en")
    mode = body.get("mode", "one_way")
    output_format = body.get("format", "token")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(
            status_code=422, detail="Field text must be a non-empty string"
        )
    if language != "en":
        raise HTTPException(status_code=422, detail="Only language en is supported")
    if mode not in MODES:
        raise HTTPException(
            status_code=422, detail=f"Field mode must be one of: {', '.join(MODES)}"
        )
    if output_format not in OUTPUT_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=f"Field format must be one of: {', '.join(OUTPUT_FORMATS)}",
        )
    # Placeholder and mask output cannot be inverted: two distinct originals
    # share one replacement, so a mapping would be ambiguous.
    if mode == "reversible" and output_format != "token":
        raise HTTPException(
            status_code=422, detail="Field format must be token when mode is reversible"
        )
    started = time.perf_counter()
    try:
        results = engine.analyze(text=text, language=language)
    except Exception as exc:  # noqa: BLE001 - any analyzer failure maps to 500
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail="Analysis failed") from exc
    detected = {item.entity_type for item in results}
    blocked = sorted(et for et in detected if action_for(policy, et) == "block")
    if blocked:
        # Reject before the anonymizer runs: no partial text may leave the service.
        raise HTTPException(
            status_code=422, detail=f"Blocked by policy: {', '.join(blocked)}"
        )
    from presidio_anonymizer.entities import ConflictResolutionStrategy

    collector = TokenCollector()
    try:
        operators = build_operators(detected, policy, output_format, collector)
        result = anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            operators=operators,
            conflict_resolution=ConflictResolutionStrategy.REMOVE_INTERSECTIONS,
        )
    except Exception as exc:  # noqa: BLE001 - any anonymizer failure maps to 500
        logger.exception("Anonymization failed")
        raise HTTPException(status_code=500, detail="Anonymization failed") from exc
    # The engine applies operators right-to-left, so the mapping arrives in
    # reverse document order; present it in the order the text reads.
    collector.sort_by_document_order(text)
    response = {"text": result.text}
    if mode == "reversible":
        response["mapping"] = collector.mapping
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "Anonymized %d chars in %.1fms: %s",
        len(text),
        elapsed_ms,
        ",".join(sorted(detected)),
    )
    return response


@app.post("/restore")
def restore(body: dict):
    text = body.get("text", "")
    mapping = body.get("mapping")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(
            status_code=422, detail="Field text must be a non-empty string"
        )
    try:
        restored = restore_text(text, mapping)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"text": restored}
