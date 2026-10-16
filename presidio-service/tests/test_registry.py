from recognizer_registry import build_registry


def test_registry_covers_expected_entities():
    registry = build_registry()
    covered = {entity for r in registry.recognizers for entity in r.supported_entities}
    assert {
        "PROJECT_CODE",
        "ORGANIZATION",
        "PERSON",
        "MALAYSIAN_PERSON",
        "NRIC",
        "SSM",
        "API_KEY",
        "PHONE_NUMBER",
        "ADDRESS",
        "PROJECT_NAME",
    } <= covered


def test_registry_adds_location_gazetteer():
    registry = build_registry()
    names = {r.name for r in registry.recognizers}
    assert "GazetteerRecognizer-LOCATION" in names


def test_location_gazetteer_matches_where_ner_misreads_context():
    from recognizers import GazetteerRecognizer

    # spaCy reads the "Malaysia" in "Ringgit Malaysia" as PERSON; the phrase
    # list must still surface it as LOCATION so it is not left unredacted.
    rec = GazetteerRecognizer(["Malaysia"], entity="LOCATION")
    results = rec.analyze("Ringgit Malaysia deal.", entities=["LOCATION"])
    assert {(r.entity_type, r.start, r.end) for r in results} == {("LOCATION", 8, 16)}
    assert all(r.score == 1.0 for r in results)


def test_project_name_span_comes_from_gazetteer():
    from recognizers import GazetteerRecognizer

    rec = GazetteerRecognizer(["Sungai Rasau Water Supply"], entity="PROJECT_NAME")
    results = rec.analyze(
        "Works on Sungai Rasau Water Supply package.", entities=["PROJECT_NAME"]
    )
    assert {(r.entity_type, r.start, r.end) for r in results} == {
        ("PROJECT_NAME", 9, 34)
    }


def test_missing_registry_file_fails_fast():
    import pytest

    with pytest.raises(Exception):
        build_registry("/nonexistent-registry.yaml")


def test_gazetteer_lists_every_labeled_company_and_state_name():
    # Boundary FPs like "B. GAMUDA BERHAD" survive only when no exact-span
    # gazetteer hit outscores the prefixed spaCy span, so every labeled
    # company and the full state name must be present here.
    import yaml

    from recognizer_registry import DEFAULT_GAZETTEER_FILE

    with open(DEFAULT_GAZETTEER_FILE, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    assert {"Gamuda Berhad", "Gamuda Engineering Sdn Bhd"} <= set(
        raw["company_names"]
    )
    assert {"Schneider Electric", "AVEVA", "SCADA Package"} <= set(
        raw["company_names"]
    )
    assert {"Selangor", "Selangor Darul Ehsan"} <= set(raw["locations"])
