"""Separated gazetteer compiler:
- Writes clean string phrases/acronyms to gazetteer.yaml
- Writes declarative regex pattern recognizers to recognizers.yaml
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

try:
    import yaml
except ImportError:
    yaml = None

STOP_WORDS: Set[str] = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "into",
    "of", "on", "or", "the", "to", "with", "&", "/"
}

CORPORATE_SUFFIX_PATTERN = re.compile(
    r"\s*,?\s*\b(sdn\s*\.?\s*bhd|sendirian\s+berhad|bhd|berhad|plc|llc|corp(\.|oration)?|inc(\.|orporated)?|ltd(\.|imited)?)\b\.?",
    flags=re.IGNORECASE,
)

CORPORATE_PREFIX_PATTERN = re.compile(
    r"^(the|messrs\.?)\s+",
    flags=re.IGNORECASE,
)


def clean_text(text: str) -> str:
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \"',.")


def strip_corporate_suffixes(name: str) -> str:
    cleaned = CORPORATE_SUFFIX_PATTERN.sub("", name)
    return clean_text(cleaned)


def strip_corporate_prefixes(name: str) -> str:
    cleaned = CORPORATE_PREFIX_PATTERN.sub("", name)
    return clean_text(cleaned)


def strip_corporate_affixes(name: str) -> str:
    cleaned = CORPORATE_SUFFIX_PATTERN.sub("", name)
    cleaned = CORPORATE_PREFIX_PATTERN.sub("", cleaned)
    return clean_text(cleaned)


def extract_clean_tokens(text: str) -> List[str]:
    tokens = re.findall(r"[A-Za-z0-9]+", text)
    return [w for w in tokens if w.lower() not in STOP_WORDS]


def build_flexible_regex(tokens: List[str]) -> str:
    if not tokens:
        return ""
    escaped_tokens = [re.escape(t) for t in tokens]
    return r"\b" + r"[-.\s]+".join(escaped_tokens) + r"\b"


def compile_and_split(gazetteer_path: Path, recognizers_path: Path) -> None:
    if not gazetteer_path.exists():
        print(f"Error: {gazetteer_path.resolve()} not found.")
        return

    if yaml is None:
        raise RuntimeError("PyYAML required. Run: pip install pyyaml")

    with open(gazetteer_path, "r", encoding="utf-8") as f:
        gaz_data = yaml.safe_load(f) or {}

    raw_companies = gaz_data.get("company_names", []) or []
    raw_projects = gaz_data.get("project_names", []) or []
    existing_acronyms = set(gaz_data.get("company_acronyms", []) or [])

    expanded_company_names: Set[str] = set()
    org_patterns: List[Dict[str, Any]] = []
    proj_patterns: List[Dict[str, Any]] = []

    # 1. Process Organizations: store base names only and generate acronyms
    for raw_comp in raw_companies:
        canonical = clean_text(str(raw_comp))
        if not canonical:
            continue

        base_name = strip_corporate_affixes(canonical)
        effective_base = base_name if base_name else canonical

        # Store ONLY the base name in the gazetteer so lookahead handles suffixes
        expanded_company_names.add(effective_base)

        base_tokens = extract_clean_tokens(effective_base)

        # Generate acronym from base name (skips generic "SB" and exact full-name duplicates)
        if len(base_tokens) > 1:
            abbr = "".join(w[0].upper() for w in base_tokens)
            if abbr.upper() != "SB" and abbr.upper() != canonical.upper():
                existing_acronyms.add(abbr)

        # Generate flexible regex for the base name
        if len(base_tokens) > 1:
            slug = re.sub(r"[^\w]+", "_", effective_base).strip("_").lower()
            org_patterns.append({
                "name": slug,
                "regex": build_flexible_regex(base_tokens),
                "score": 0.85,
            })

    # 2. Process Projects: derive regexes without abbreviations
    for raw_proj in raw_projects:
        canonical = clean_text(str(raw_proj))
        if not canonical:
            continue
        proj_tokens = extract_clean_tokens(canonical)
        if len(proj_tokens) > 1:
            slug = re.sub(r"[^\w]+", "_", canonical).strip("_").lower()
            proj_patterns.append({
                "name": slug,
                "regex": build_flexible_regex(proj_tokens),
                "score": 0.85,
            })

    # 3. Update gazetteer.yaml
    gaz_data["company_names"] = sorted(expanded_company_names)
    gaz_data["company_acronyms"] = sorted(existing_acronyms)
    if raw_projects:
        gaz_data["project_names"] = sorted({clean_text(str(p)) for p in raw_projects if clean_text(str(p))})
    gaz_data.pop("gazetteer", None)

    with open(gazetteer_path, "w", encoding="utf-8") as f:
        yaml.dump(gaz_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    print(f"Updated gazetteer file: {gazetteer_path.resolve()}")

    # 4. Update recognizers.yaml (Presidio Declarative Recognizers)
    rec_data: Dict[str, Any] = {"recognizers": []}
    if recognizers_path.exists():
        with open(recognizers_path, "r", encoding="utf-8") as f:
            rec_data = yaml.safe_load(f) or {"recognizers": []}

    existing_recs = [
        r for r in rec_data.get("recognizers", [])
        if r.get("name") not in ("OrganizationFlexibleRegexRecognizer", "ProjectNameFlexibleRegexRecognizer")
    ]

    if org_patterns:
        existing_recs.append({
            "name": "OrganizationFlexibleRegexRecognizer",
            "supported_language": "en",
            "supported_entity": "ORGANIZATION",
            "patterns": org_patterns,
        })

    if proj_patterns:
        existing_recs.append({
            "name": "ProjectNameFlexibleRegexRecognizer",
            "supported_language": "en",
            "supported_entity": "PROJECT_NAME",
            "patterns": proj_patterns,
        })

    rec_data["recognizers"] = existing_recs

    with open(recognizers_path, "w", encoding="utf-8") as f:
        yaml.dump(rec_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    print(f"Updated recognizers file: {recognizers_path.resolve()}")


if __name__ == "__main__":
    gaz_file = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("gazetteer.yaml")
    rec_file = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("recognizers.yaml")
    compile_and_split(gaz_file, rec_file)