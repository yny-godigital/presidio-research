"""Cross-entity conflict resolution for /analyze results.

Presidio's AnalyzerEngine only dedupes same-type contained spans
(EntityRecognizer.remove_duplicates), so /analyze returns overlapping spans:
a gazetteer substring inside a spaCy span, or two recognizers typing one span
differently. Exact-match eval scoring counts each overlap as an FP plus an FN,
so resolve to one span before responding.

Rule: sort by entity priority, then score, then length; each span survives
unless an overlapping survivor beats it. Priority beats score on purpose for
*overlapping* spans: a 0.6 ADDRESS detection must still swallow its 0.85 spaCy
sub-entities, and a 1.0 PROJECT_NAME gazetteer hit must beat a 0.85 spaCy
ORGANIZATION even when the ORGANIZATION span is longer ("Proposed Rasau
Intake" containing "Rasau Intake").

Two refinements: spans with *identical* offsets are decided by score first —
the 1.0 gazetteer typing ("Selangor Darul Ehsan" as LOCATION) beats spaCy's
0.85 mistyping of the same characters. And precise ID entities (NRIC, SSM,
API keys, ...) coexist with a container instead of being swallowed by it —
dropping "1102650-A" because it sits inside an ADDRESS span would trade an
FP for a leak.
"""

PRIORITY = (
    "NRIC",
    "SSM",
    "API_KEY",
    "PROJECT_CODE",
    "PHONE_NUMBER",
    "EMAIL_ADDRESS",
    "PROJECT_NAME",
    "ADDRESS",
    "ORGANIZATION",
    "LOCATION",
    "MALAYSIAN_PERSON",
    "PERSON",
    "DATE_TIME",
    "NRP",
    "URL",
)

#: Contained spans of these types survive alongside their container.
ID_TYPES = frozenset(
    {"NRIC", "SSM", "API_KEY", "PROJECT_CODE", "PHONE_NUMBER", "EMAIL_ADDRESS"}
)


def _rank(entity_type: str) -> int:
    try:
        return PRIORITY.index(entity_type)
    except ValueError:
        return len(PRIORITY)


def _overlaps(a, b) -> bool:
    return a.start < b.end and b.start < a.end


def _beats(a, b, *, score_first: bool) -> bool:
    """True when span a survives an overlap with span b."""
    if score_first and a.score != b.score:
        return a.score > b.score
    rank_a, rank_b = _rank(a.entity_type), _rank(b.entity_type)
    if rank_a != rank_b:
        return rank_a < rank_b
    if a.score != b.score:
        return a.score > b.score
    len_a, len_b = a.end - a.start, b.end - b.start
    if len_a != len_b:
        return len_a > len_b
    return a.start <= b.start


def _coexists(inner, outer) -> bool:
    """A precise ID inside a larger detection is kept alongside it."""
    return (
        inner.entity_type in ID_TYPES
        and outer.entity_type not in ID_TYPES
        and (inner.start, inner.end) != (outer.start, outer.end)
    )


def resolve_conflicts(results: list) -> list:
    """Return the non-overlapping winners of analyzer results."""
    ordered = sorted(
        results,
        key=lambda r: (_rank(r.entity_type), -r.score, -(r.end - r.start), r.start),
    )
    kept: list = []
    for candidate in ordered:
        defeated: list = []
        loses = False
        for survivor in kept:
            if not _overlaps(candidate, survivor):
                continue
            identical = (
                candidate.start == survivor.start and candidate.end == survivor.end
            )
            candidate_contains = (
                candidate.start <= survivor.start and candidate.end >= survivor.end
            )
            survivor_contains = (
                survivor.start <= candidate.start and survivor.end >= candidate.end
            )
            if identical:
                # Same characters typed twice: trust the higher score, but a
                # losing ID (NRIC vs SSM on one 12-digit number) is genuinely
                # ambiguous, so it survives alongside the winner.
                if _beats(survivor, candidate, score_first=True):
                    if candidate.entity_type in ID_TYPES:
                        continue
                    loses = True
                    break
                if survivor.entity_type in ID_TYPES:
                    continue
                defeated.append(survivor)
                continue
            if candidate_contains and _coexists(survivor, candidate):
                continue
            if survivor_contains and _coexists(candidate, survivor):
                continue
            if _beats(survivor, candidate, score_first=False):
                loses = True
                break
            defeated.append(survivor)
        if loses:
            continue
        kept = [s for s in kept if not any(s is d for d in defeated)]
        kept.append(candidate)
    return sorted(kept, key=lambda r: (r.start, r.end))
