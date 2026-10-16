from fastapi.testclient import TestClient
from presidio_anonymizer import AnonymizerEngine

import app as service_app


class StubResult:
    def __init__(self, entity_type, start, end, score):
        self.entity_type = entity_type
        self.start = start
        self.end = end
        self.score = score


class StubEngine:
    def __init__(self, results=None):
        self.results = (
            results if results is not None else [StubResult("PERSON", 0, 8, 0.85)]
        )

    def analyze(self, text, language, entities=None, **kwargs):
        return self.results


DEFAULT_POLICY = {"PERSON": "pseudonymize", "EMAIL_ADDRESS": "pseudonymize"}


def make_client(results=None, policy=None):
    stub = StubEngine(results)
    service_app.app.dependency_overrides[service_app.get_engine] = lambda: stub
    service_app.app.dependency_overrides[service_app.get_policy] = lambda: (
        DEFAULT_POLICY if policy is None else policy
    )
    service_app.app.dependency_overrides[service_app.get_anonymizer] = lambda: (
        AnonymizerEngine()
    )
    return TestClient(service_app.app), stub


def test_analyze_returns_gateway_shape():
    client, _ = make_client()
    response = client.post(
        "/analyze", json={"text": "John Tan paid.", "language": "en"}
    )
    assert response.status_code == 200
    assert response.json() == [
        {"entity_type": "PERSON", "start": 0, "end": 8, "score": 0.85}
    ]


def test_analyze_resolves_overlapping_spans_to_one_winner():
    client, _ = make_client(
        results=[
            StubResult("PERSON", 65, 85, 0.85),
            StubResult("LOCATION", 65, 85, 1.0),
        ]
    )
    response = client.post(
        "/analyze", json={"text": "x" * 85, "language": "en"}
    )
    assert response.status_code == 200
    assert response.json() == [
        {"entity_type": "LOCATION", "start": 65, "end": 85, "score": 1.0}
    ]


def test_analyze_rejects_empty_text_and_other_languages():
    client, _ = make_client()
    assert (
        client.post("/analyze", json={"text": "", "language": "en"}).status_code == 422
    )
    assert (
        client.post("/analyze", json={"text": "hi", "language": "ms"}).status_code
        == 422
    )


def test_anonymize_default_mode_returns_token_text():
    client, _ = make_client()
    response = client.post(
        "/anonymize", json={"text": "John Tan paid.", "language": "en"}
    )
    assert response.status_code == 200
    assert response.json() == {"text": "<PERSON_001> paid."}


def test_anonymize_reversible_returns_text_and_mapping():
    client, _ = make_client(
        results=[
            StubResult("PERSON", 0, 8, 0.85),
            StubResult("EMAIL_ADDRESS", 14, 30, 0.9),
        ]
    )
    response = client.post(
        "/anonymize",
        json={"text": "John Tan paid john@example.com.", "mode": "reversible"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "text": "<PERSON_001> paid <EMAIL_ADDRESS_001>.",
        "mapping": [
            {"token": "<PERSON_001>", "entity_type": "PERSON", "original": "John Tan"},
            {
                "token": "<EMAIL_ADDRESS_001>",
                "entity_type": "EMAIL_ADDRESS",
                "original": "john@example.com",
            },
        ],
    }


def test_anonymize_dedupes_repeated_value_within_request():
    client, _ = make_client(
        results=[
            StubResult("PERSON", 0, 8, 0.85),
            StubResult("PERSON", 13, 21, 0.85),
        ]
    )
    response = client.post(
        "/anonymize", json={"text": "John Tan met John Tan.", "mode": "reversible"}
    )
    assert response.json() == {
        "text": "<PERSON_001> met <PERSON_001>.",
        "mapping": [
            {"token": "<PERSON_001>", "entity_type": "PERSON", "original": "John Tan"}
        ],
    }


def test_anonymize_trims_overlapping_spans_disjoint():
    client, _ = make_client(
        results=[
            StubResult("PROJECT_NAME", 24, 46, 1.0),
            StubResult("ORGANIZATION", 28, 53, 0.85),
        ]
    )
    text = "Proposed Development of Sg. Rasau Water Supply Scheme (Stage 1)"
    anonymized = client.post(
        "/anonymize", json={"text": text, "mode": "reversible"}
    ).json()
    assert anonymized["text"] == (
        "Proposed Development of <PROJECT_NAME_001><ORGANIZATION_001> (Stage 1)"
    )
    restored = client.post(
        "/restore",
        json={"text": anonymized["text"], "mapping": anonymized["mapping"]},
    )
    assert restored.json() == {"text": text}


def test_anonymize_block_action_rejects_before_replacement():
    client, _ = make_client(policy={"PERSON": "block"})
    response = client.post("/anonymize", json={"text": "John Tan paid."})
    assert response.status_code == 422
    assert "PERSON" in response.json()["detail"]


def test_anonymize_redact_writes_marker_and_never_maps():
    client, _ = make_client(policy={"PERSON": "redact"})
    response = client.post(
        "/anonymize", json={"text": "John Tan paid.", "mode": "reversible"}
    )
    assert response.status_code == 200
    assert response.json() == {"text": "[REDACTED] paid.", "mapping": []}


def test_anonymize_placeholder_and_mask_formats():
    client, _ = make_client()
    placeholder = client.post(
        "/anonymize", json={"text": "John Tan paid.", "format": "placeholder"}
    )
    assert placeholder.json() == {"text": "<PERSON> paid."}
    mask = client.post("/anonymize", json={"text": "John Tan paid.", "format": "mask"})
    assert mask.json() == {"text": "******** paid."}


def test_anonymize_rejects_unknown_values_and_non_token_reversible():
    client, _ = make_client()
    base = {"text": "John Tan paid."}
    assert client.post("/anonymize", json={**base, "mode": "alias"}).status_code == 422
    assert (
        client.post("/anonymize", json={**base, "format": "base64"}).status_code == 422
    )
    assert (
        client.post(
            "/anonymize", json={**base, "mode": "reversible", "format": "mask"}
        ).status_code
        == 422
    )


def test_anonymize_rejects_empty_text_and_other_languages():
    client, _ = make_client()
    assert (
        client.post("/anonymize", json={"text": "", "language": "en"}).status_code
        == 422
    )
    assert (
        client.post("/anonymize", json={"text": "hi", "language": "ms"}).status_code
        == 422
    )


def test_restore_returns_original_text():
    client, _ = make_client()
    response = client.post(
        "/restore",
        json={
            "text": "<PERSON_001> paid <EMAIL_ADDRESS_001>.",
            "mapping": [
                {
                    "token": "<PERSON_001>",
                    "entity_type": "PERSON",
                    "original": "John Tan",
                },
                {
                    "token": "<EMAIL_ADDRESS_001>",
                    "entity_type": "EMAIL_ADDRESS",
                    "original": "john@example.com",
                },
            ],
        },
    )
    assert response.status_code == 200
    assert response.json() == {"text": "John Tan paid john@example.com."}


def test_restore_roundtrips_anonymize_output():
    client, _ = make_client(
        results=[
            StubResult("PERSON", 0, 8, 0.85),
            StubResult("EMAIL_ADDRESS", 14, 30, 0.9),
        ]
    )
    anonymized = client.post(
        "/anonymize",
        json={"text": "John Tan paid john@example.com.", "mode": "reversible"},
    ).json()
    restored = client.post(
        "/restore", json={"text": anonymized["text"], "mapping": anonymized["mapping"]}
    )
    assert restored.json() == {"text": "John Tan paid john@example.com."}


def test_restore_leaves_unmapped_token_untouched():
    client, _ = make_client()
    response = client.post(
        "/restore", json={"text": "keep <PERSON_009>", "mapping": []}
    )
    assert response.status_code == 200
    assert response.json() == {"text": "keep <PERSON_009>"}


def test_restore_rejects_bad_mappings_and_empty_text():
    client, _ = make_client()
    conflicting = client.post(
        "/restore",
        json={
            "text": "<PERSON_001>",
            "mapping": [
                {"token": "<PERSON_001>", "entity_type": "PERSON", "original": "A"},
                {"token": "<PERSON_001>", "entity_type": "PERSON", "original": "B"},
            ],
        },
    )
    assert conflicting.status_code == 422
    assert (
        client.post("/restore", json={"text": "x", "mapping": "nope"}).status_code
        == 422
    )
    assert (
        client.post(
            "/restore", json={"text": "x", "mapping": [{"token": "<PERSON_001>"}]}
        ).status_code
        == 422
    )
    assert (
        client.post("/restore", json={"text": "  ", "mapping": []}).status_code == 422
    )
