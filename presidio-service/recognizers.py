"""Custom recognizers that declarative YAML cannot express.

Everything pattern-shaped lives in recognizers.yaml. This module holds
only the two things a provider file cannot supply: the automaton
gazetteer, which needs its phrase list at construction time, and
recognizers carrying validate_result() checks — a real calendar date, a
JPN place-of-birth code, an SSM registration year and entity type —
which no YAML pattern can perform.
"""

from datetime import UTC, datetime

from presidio_analyzer import (
    EntityRecognizer,
    Pattern,
    PatternRecognizer,
    RecognizerResult,
)

from gazetteer import build_matcher

DENY_LIST_SCORE = 1.0


class GazetteerRecognizer(EntityRecognizer):
    """Phrase-list matching backed by an Aho-Corasick automaton.

    The automaton is compiled once in load() so list edits cost a restart
    and every request pays only a single linear scan.
    """

    def __init__(self, phrases: list[str], entity: str = "ORGANIZATION"):
        # EntityRecognizer.__init__ calls load(), so both must exist first.
        self._phrases = list(phrases)
        self._entity = entity
        self._matcher = None
        super().__init__(
            supported_entities=[entity],
            supported_language="en",
            name=f"GazetteerRecognizer-{entity}",
        )

    def load(self) -> None:
        self._matcher = build_matcher(self._phrases)

    def analyze(
        self, text: str, entities: list[str], nlp_artifacts=None
    ) -> list[RecognizerResult]:
        if self._matcher is None:
            self.load()
        return [
            RecognizerResult(
                entity_type=self._entity, start=start, end=end, score=DENY_LIST_SCORE
            )
            for start, end, _ in self._matcher.match(text)
        ]


#: MyKad place-of-birth codes, transcribed from the National Registration
#: Department tables (jpn.gov.my state-code and country-code pages): 01-16
#: states and federal territories, 21-59 secondary state codes, and the 30
#: country/stateless/unspecified codes 60-68, 71, 72, 74-79, 82, 83-93, 98,
#: 99. Anything outside is unassigned and marks a candidate invalid — that
#: gap is what makes a bare 12-digit match safe to offer. Ported from
#: presidio-malaysia 1.0.0 (MIT), which read both JPN tables on 2026-08-20;
#: widen this set if an ever-retired allocation shows up on a real card.
_NRIC_PB_CODES = frozenset(
    f"{n:02d}"
    for n in (
        *range(1, 17),
        *range(21, 60),
        *range(60, 69),
        71,
        72,
        *range(74, 80),
        82,
        *range(83, 94),
        98,
        99,
    )
)


def _valid_date(yymmdd: str) -> bool:
    """True when YYMMDD is a real calendar date under some non-future century reading."""
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    today = datetime.now(UTC).date()
    for century in (1900, 2000):
        try:
            candidate = datetime(century + yy, mm, dd, tzinfo=UTC).date()
        except ValueError:
            continue
        if candidate <= today:
            return True
    return False


class MalaysianNricRecognizer(PatternRecognizer):
    """Malaysian MyKad / NRIC numbers, hyphenated or compact.

    The form is YYMMDD-PB-####G and, contrary to popular belief, it carries no
    check digit — so validation is structural: a real, non-future birth date and
    an assigned place-of-birth pair. Presidio raises a passing match to 1.0 and
    drops a failing one, which is what keeps the bare 12-digit compact pattern
    from flagging every long number in a contract.

    The compact 12-digit shape also matches new-format SSM numbers; both
    recognizers run and their validations almost always separate the two (an
    NRIC needs a valid date, an SSM a plausible registration year), with any
    residual overlap left to Presidio's normal resolution.
    """

    PATTERNS = [
        Pattern("nric hyphenated", r"\b\d{6}-\d{2}-\d{4}\b", 0.95),
        # Low base score; validate_result is what makes this pattern safe.
        Pattern("nric compact", r"\b\d{12}\b", 0.2),
    ]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="NRIC",
            patterns=self.PATTERNS,
            name="MalaysianNricRecognizer",
        )

    def validate_result(self, pattern_text: str) -> bool:
        digits = pattern_text.replace("-", "")
        return _valid_date(digits[:6]) and digits[6:8] in _NRIC_PB_CODES


#: Entity-type codes in the post-2019 12-digit SSM registration number.
_SSM_ENTITY_TYPE_CODES = frozenset(
    {
        "01",  # local company
        "02",  # foreign company
        "03",  # business
        "04",  # limited liability partnership
        "05",  # foreign LLP
        "06",  # international LLP
    }
)


class MalaysianSsmRecognizer(PatternRecognizer):
    """SSM company / business registration numbers, new and old formats.

    New format (post-2019) is YYYYTTNNNNNN: registration year and entity-type
    code are both validated, so the 12-digit shape (shared with a compact NRIC)
    can carry a low base score and still be safe. Old formats (NNNNNNN-X for
    companies, NNNNNNNN-P for businesses) have no internal structure to check;
    they keep their modest base score and rely on the context words to reach a
    useful threshold.
    """

    PATTERNS = [
        Pattern("ssm new 12-digit", r"\b\d{12}\b", 0.2),
        Pattern("ssm old", r"\b\d{6,8}-[A-Za-z]\b", 0.3),
    ]

    # The context enhancer matches single-token lemmas, so a multi-word entry
    # never fires on its own; bare "company" covers the "company no" phrasing.
    CONTEXT = ["ssm", "roc", "rob", "company", "company no", "syarikat"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="SSM",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            name="MalaysianSsmRecognizer",
        )

    def validate_result(self, pattern_text: str) -> bool | None:
        if not (len(pattern_text) == 12 and pattern_text.isdigit()):
            return None  # old format: context words, not structure, decide
        year, type_code = int(pattern_text[:4]), pattern_text[4:6]
        return (
            1900 <= year <= datetime.now(UTC).year
            and type_code in _SSM_ENTITY_TYPE_CODES
        )
