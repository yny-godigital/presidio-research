"""Custom recognizers that declarative YAML cannot express."""

import re
from datetime import UTC, datetime

from presidio_analyzer import (
    EntityRecognizer,
    Pattern,
    PatternRecognizer,
    RecognizerResult,
)
from gazetteer import build_matcher

DENY_LIST_SCORE = 1.0

# Suffix lookahead pattern: handles optional leading commas, arbitrary whitespace/newlines,
# optional industry qualifiers (Engineering, Trading, etc.), and corporate legal forms with optional trailing dot.
SUFFIX_LOOKAHEAD_PATTERN = re.compile(
    r"^(\s*,?\s*(?:(?:enterprise|trading|construction|engineering)\s*,?\s*)?(?:sdn\s*\.?\s*bhd|sendirian\s+berhad|bhd|berhad|plc|llc|corp(?:\.|oration)?|inc(?:\.|orporated)?|ltd(?:\.|imited)?)\b\.?)",
    flags=re.IGNORECASE,
)

# Prefix lookbehind pattern: matches leading corporate designations like "The" or "Messrs."
PREFIX_LOOKBEHIND_PATTERN = re.compile(
    r"\b(the|messrs\.?)\s+$",
    flags=re.IGNORECASE,
)


def is_all_caps_line(text: str, start: int, end: int) -> bool:
    """True if the line containing the span has multiple words and is entirely uppercase."""
    line_start = text.rfind("\n", 0, start)
    line_start = 0 if line_start == -1 else line_start + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end == -1 else line_end
    line = text[line_start:line_end].strip()
    words = line.split()
    return len(words) > 2 and line.isupper()


class GazetteerRecognizer(EntityRecognizer):
    def __init__(
        self,
        phrases: list[str],
        entity: str,
        case_sensitive: bool = False,
        name: str | None = None,
        allow_suffix_expansion: bool = False,
        allow_prefix_expansion: bool = False,
    ):
        super().__init__(
            supported_entities=[entity],
            supported_language="en",
            name=name or f"GazetteerRecognizer-{entity}{'-case' if case_sensitive else ''}",
        )
        self.matcher = build_matcher(phrases, case_sensitive=case_sensitive)
        self.case_sensitive = case_sensitive
        self.allow_suffix_expansion = allow_suffix_expansion
        self.allow_prefix_expansion = allow_prefix_expansion

    def load(self) -> None:
        pass

    def analyze(self, text: str, entities: list[str], nlp_artifacts=None):
        results = []
        if self.supported_entities[0] not in entities:
            return results

        for start, end, matched_text in self.matcher.match(text):
            trailing_text = text[end:]
            suffix_match = None
            if self.allow_suffix_expansion:
                suffix_match = SUFFIX_LOOKAHEAD_PATTERN.match(trailing_text)

            # Guardrail: Suppress <=2 char acronyms in all-caps text unless followed by a corporate suffix
            if self.case_sensitive and len(matched_text) <= 2:
                if is_all_caps_line(text, start, end) and not suffix_match:
                    continue

            expanded_start = start
            expanded_end = end
            affix_applied = False

            # Prefix expansion
            if self.allow_prefix_expansion:
                prefix_match = PREFIX_LOOKBEHIND_PATTERN.search(text[:start])
                if prefix_match:
                    expanded_start = prefix_match.start()
                    affix_applied = True

            # Suffix expansion
            if suffix_match:
                expanded_end = end + suffix_match.end()
                affix_applied = True
            elif self.allow_suffix_expansion and re.search(r"\b(bhd|sdn|ltd|corp|inc|co)\b$", matched_text, re.IGNORECASE):
                # Fallback: If matched entity already ends with an abbreviation token, absorb a trailing period
                if trailing_text.startswith(".") and not trailing_text.startswith(".."):
                    expanded_end = end + 1
                    affix_applied = True

            origin = f"{self.name}+AffixLookahead" if affix_applied else self.name
            score = 1.0

            result = RecognizerResult(
                entity_type=self.supported_entities[0],
                start=expanded_start,
                end=expanded_end,
                score=score,
            )
            result.recognition_metadata = {"recognizer_name": origin}
            results.append(result)

        return results


#: MyKad place-of-birth codes
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
    PATTERNS = [
        Pattern("nric hyphenated", r"\b\d{6}[-–—\s]\d{2}[-–—\s]\d{4}\b", 0.95),
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
    PATTERNS = [
        Pattern("ssm new 12-digit", r"\b\d{12}\b", 0.2),
        Pattern("ssm old", r"\b\d{6,8}-[A-Za-z]\b", 0.3),
    ]

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
            return None
        year, type_code = int(pattern_text[:4]), pattern_text[4:6]
        return (
            1900 <= year <= datetime.now(UTC).year
            and type_code in _SSM_ENTITY_TYPE_CODES
        )