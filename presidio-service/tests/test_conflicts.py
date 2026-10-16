from conflicts import resolve_conflicts


class Span:
    def __init__(self, entity_type, start, end, score):
        self.entity_type = entity_type
        self.start = start
        self.end = end
        self.score = score

    def key(self):
        return (self.entity_type, self.start, self.end)


def keys(results):
    return {r.key() for r in results}


def test_same_span_cross_type_prefers_location_over_person():
    results = resolve_conflicts(
        [Span("PERSON", 65, 85, 0.85), Span("LOCATION", 65, 85, 1.0)]
    )
    assert keys(results) == {("LOCATION", 65, 85)}


def test_same_span_cross_type_prefers_organization_over_person():
    results = resolve_conflicts(
        [Span("PERSON", 1769, 1782, 0.85), Span("ORGANIZATION", 1769, 1782, 1.0)]
    )
    assert keys(results) == {("ORGANIZATION", 1769, 1782)}


def test_project_name_beats_overlapping_organization_despite_shorter_span():
    # "Proposed Rasau Intake" (ORG) contains the "Rasau Intake" label span.
    results = resolve_conflicts(
        [Span("ORGANIZATION", 130, 151, 0.85), Span("PROJECT_NAME", 139, 151, 1.0)]
    )
    assert keys(results) == {("PROJECT_NAME", 139, 151)}


def test_project_name_beats_partially_overlapping_organization():
    results = resolve_conflicts(
        [Span("ORGANIZATION", 28, 53, 0.85), Span("PROJECT_NAME", 24, 46, 1.0)]
    )
    assert keys(results) == {("PROJECT_NAME", 24, 46)}


def test_same_type_overlap_keeps_higher_score_not_longer_span():
    # Gazetteer exact span beats the "BETWEEN\n"-prefixed spaCy span.
    results = resolve_conflicts(
        [Span("ORGANIZATION", 392, 418, 1.0), Span("ORGANIZATION", 400, 443, 0.85)]
    )
    assert keys(results) == {("ORGANIZATION", 392, 418)}


def test_address_swallows_subsumed_person_org_and_datetime():
    results = resolve_conflicts(
        [
            Span("ADDRESS", 504, 621, 0.6),
            Span("PERSON", 504, 517, 0.85),
            Span("ORGANIZATION", 537, 552, 0.85),
            Span("DATE_TIME", 602, 607, 0.85),
        ]
    )
    assert keys(results) == {("ADDRESS", 504, 621)}


def test_id_inside_container_coexists_instead_of_dying():
    results = resolve_conflicts(
        [Span("ADDRESS", 441, 621, 0.6), Span("SSM", 445, 453, 0.9)]
    )
    assert keys(results) == {("ADDRESS", 441, 621), ("SSM", 445, 453)}


def test_organization_container_beats_location_containee():
    results = resolve_conflicts(
        [Span("ORGANIZATION", 1061, 1092, 1.0), Span("LOCATION", 1076, 1084, 1.0)]
    )
    assert keys(results) == {("ORGANIZATION", 1061, 1092)}


def test_identical_spans_trust_higher_score_over_priority():
    # spaCy types the state name ORGANIZATION 0.85; the gazetteer types the
    # same characters LOCATION 1.0. Score decides identical spans.
    results = resolve_conflicts(
        [Span("ORGANIZATION", 35, 55, 0.85), Span("LOCATION", 35, 55, 1.0)]
    )
    assert keys(results) == {("LOCATION", 35, 55)}


def test_identical_id_spans_coexist_as_ambiguous():
    results = resolve_conflicts(
        [Span("NRIC", 0, 12, 0.9), Span("SSM", 0, 12, 0.2)]
    )
    assert keys(results) == {("NRIC", 0, 12), ("SSM", 0, 12)}


def test_disjoint_spans_all_survive_in_document_order():
    results = resolve_conflicts(
        [Span("PERSON", 10, 15, 0.9), Span("LOCATION", 0, 5, 1.0)]
    )
    assert [(r.start, r.end) for r in results] == [(0, 5), (10, 15)]
