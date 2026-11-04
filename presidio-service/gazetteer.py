"""Multi-phrase matching over PDF-extracted text with optional case sensitivity.

Extraction mangles spacing ("Pro - Cahaya Industries Sdn. Bhd."),
so text and phrases are normalized before matching, with an index table
mapping reduced offsets back to the original text.
"""

import re
import ahocorasick

_Phrase = tuple[int, int, str]


class PhraseMatcher:
    def __init__(self, phrases: list[str], case_sensitive: bool = False):
        self._automaton = ahocorasick.Automaton()
        self._empty = True
        self.case_sensitive = case_sensitive
        seen: set[str] = set()

        for phrase in phrases:
            key = _reduce(phrase, case_sensitive=self.case_sensitive)
            if key and key not in seen:
                seen.add(key)
                self._automaton.add_word(key, phrase)
                self._empty = False

        if not self._empty:
            self._automaton.make_automaton()

    def match(self, text: str) -> list[_Phrase]:
        if self._empty:
            return []

        reduced, index = _reduce_with_index(text, case_sensitive=self.case_sensitive)
        best: dict[int, _Phrase] = {}

        for end, phrase in self._automaton.iter(reduced):
            start = end - len(_reduce(phrase, case_sensitive=self.case_sensitive)) + 1
            prev = best.get(start)
            if prev is None or end > prev[1]:
                best[start] = (start, end, phrase)

        hits: list[_Phrase] = []
        for start, end, phrase in best.values():
            orig_start = index[start]
            orig_end = index[end] + 1

            if phrase.rstrip().endswith("."):
                while orig_end < len(text) and text[orig_end] == ".":
                    orig_end += 1

            if _has_word_boundary(text, orig_start) and _has_word_boundary(text, orig_end):
                hits.append((orig_start, orig_end, text[orig_start:orig_end]))

        return sorted(hits)


def build_matcher(phrases: list[str], case_sensitive: bool = False) -> PhraseMatcher:
    return PhraseMatcher(phrases, case_sensitive=case_sensitive)


def _reduce(s: str, case_sensitive: bool = False) -> str:
    cleaned = s if case_sensitive else s.lower()
    return re.sub(r"[^0-9a-zA-Z]+" if case_sensitive else r"[^0-9a-z]+", "", cleaned)


def _reduce_with_index(s: str, case_sensitive: bool = False) -> tuple[str, list[int]]:
    chars: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(s):
        if ch.isalnum():
            chars.append(ch if case_sensitive else ch.lower())
            index.append(i)
    return "".join(chars), index


def _has_word_boundary(text: str, pos: int) -> bool:
    if pos <= 0 or pos >= len(text):
        return True
    return not (text[pos - 1].isalnum() and text[pos].isalnum())