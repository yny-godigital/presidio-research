"""Case-insensitive multi-phrase matching over PDF-extracted text.

Extraction mangles spacing ("Pro - Cahaya   Industries  Sdn. Bhd."),
so both the text and every phrase are reduced to letters-and-digits runs
before matching, with an index table mapping reduced offsets back to the
original text. A trailing boundary check keeps short acronyms ("GME")
from matching inside longer tokens ("GMEX").
"""

import re

import ahocorasick

_Phrase = tuple[int, int, str]


class PhraseMatcher:
    def __init__(self, phrases: list[str]):
        self._automaton = ahocorasick.Automaton()
        self._empty = True
        seen: set[str] = set()
        for phrase in phrases:
            key = _reduce(phrase)
            if key and key not in seen:
                seen.add(key)
                self._automaton.add_word(key, phrase)
                self._empty = False
        if not self._empty:
            self._automaton.make_automaton()

    def match(self, text: str) -> list[_Phrase]:
        if self._empty:
            return []
        reduced, index = _reduce_with_index(text)
        best: dict[int, _Phrase] = {}
        for end, phrase in self._automaton.iter(reduced):
            start = end - len(_reduce(phrase)) + 1
            prev = best.get(start)
            if prev is None or end > prev[1]:
                best[start] = (start, end, phrase)
        hits: list[_Phrase] = []
        for start, end, phrase in best.values():
            orig_start = index[start]
            orig_end = index[end] + 1
            # The reduced match drops terminal punctuation, but canonical
            # phrases ending in a period ("Bhd.") own that dot: extend over
            # it so spans match label slices exactly. Phrases without a
            # terminal period ("PCI") never extend.
            if phrase.rstrip().endswith("."):
                while orig_end < len(text) and text[orig_end] == ".":
                    orig_end += 1
            if _has_word_boundary(text, orig_start) and _has_word_boundary(
                text, orig_end
            ):
                hits.append((orig_start, orig_end, text[orig_start:orig_end]))
        return sorted(hits)


def build_matcher(phrases: list[str]) -> PhraseMatcher:
    return PhraseMatcher(phrases)


def _reduce(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", s.lower())


def _reduce_with_index(s: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(s):
        if ch.isalnum():
            chars.append(ch.lower())
            index.append(i)
    return "".join(chars), index


def _has_word_boundary(text: str, pos: int) -> bool:
    if pos <= 0 or pos >= len(text):
        return True
    return not (text[pos - 1].isalnum() and text[pos].isalnum())
