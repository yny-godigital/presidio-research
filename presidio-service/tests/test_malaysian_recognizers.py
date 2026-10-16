"""Validation-backed Malaysian recognizers: patterns plus validate_result().

The YAML registry holds every declarative pattern; these tests pin the
behavior only Python can express — which candidates the validators drop.
"""

from recognizers import MalaysianNricRecognizer, MalaysianSsmRecognizer


def _spans(recognizer, text):
    results = recognizer.analyze(text, entities=recognizer.supported_entities)
    return sorted((r.entity_type, r.start, r.end, r.score) for r in results)


def test_valid_nric_passes_validation_at_max_score():
    assert _spans(MalaysianNricRecognizer(), "Mykad 900101-10-1234.") == [
        ("NRIC", 6, 20, 1.0)
    ]


def test_nric_with_impossible_date_is_dropped():
    assert _spans(MalaysianNricRecognizer(), "ref 999999-99-9999") == []


def test_nric_with_unassigned_birth_place_code_is_dropped():
    # 18 sits between the primary (01-16) and secondary (21-59) state codes.
    assert _spans(MalaysianNricRecognizer(), "ref 900101-18-1234") == []


def test_compact_nric_detected_only_when_structurally_valid():
    recognizer = MalaysianNricRecognizer()
    assert _spans(recognizer, "no. 900101101234 ok") == [("NRIC", 4, 16, 1.0)]
    # Fails the date check; without validation any 12-digit run would match.
    assert _spans(recognizer, "amount 123456789012") == []


def test_new_format_ssm_passes_year_and_entity_type_checks():
    assert _spans(MalaysianSsmRecognizer(), "SSM 202301123456") == [("SSM", 4, 16, 1.0)]


def test_new_format_ssm_rejects_unknown_type_and_future_year():
    recognizer = MalaysianSsmRecognizer()
    assert _spans(recognizer, "reg 202399123456") == []  # 99 is no entity type
    assert _spans(recognizer, "reg 209901123456") == []  # not yet registered


def test_old_format_ssm_keeps_base_score_for_context_to_lift():
    # No structure to validate: the match survives at its pattern score so the
    # context enhancer, not this check, decides whether it surfaces.
    assert _spans(MalaysianSsmRecognizer(), "Company no 1998123-A.") == [
        ("SSM", 11, 20, 0.3)
    ]
