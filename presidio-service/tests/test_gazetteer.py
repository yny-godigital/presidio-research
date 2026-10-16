from gazetteer import build_matcher


def test_exact_and_case_insensitive_match():
    matcher = build_matcher(["Gamuda M&E Sdn. Bhd.", "PCI"])
    assert (0, 20, "Gamuda M&E Sdn. Bhd.") in matcher.match(
        "Gamuda M&E Sdn. Bhd. hired PCI."
    )


def test_extraction_spacing_variants_match():
    matcher = build_matcher(["Pro-Cahaya Industries Sdn. Bhd."])
    hits = matcher.match("met Pro - Cahaya   Industries  Sdn. Bhd. today")
    assert [(s, e) for s, e, _ in hits] == [(4, 40)]


def test_longest_match_wins_and_word_boundaries_hold():
    matcher = build_matcher(["GME", "GME Sub-Co"])
    hits = matcher.match("GME Sub-Co arrived; GMEX stayed")
    assert [(s, e) for s, e, _ in hits] == [(0, 10)]


def test_empty_list_matches_nothing():
    assert build_matcher([]).match("Gamuda M&E Sdn. Bhd.") == []
