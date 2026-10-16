from recognizers import GazetteerRecognizer


def test_gazetteer_recognizer_emits_organization_at_full_score():
    rec = GazetteerRecognizer(["Gamuda M&E Sdn. Bhd.", "PCI"])
    results = rec.analyze(
        "Hired Gamuda M&E Sdn. Bhd. via PCI.", entities=["ORGANIZATION"]
    )
    by_type = {(r.entity_type, r.start, r.end) for r in results}
    assert ("ORGANIZATION", 6, 26) in by_type
    assert ("ORGANIZATION", 31, 34) in by_type
    assert all(r.score == 1.0 for r in results)
