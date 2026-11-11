from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple
import requests
import yaml

from abbreviate import clean_text

# ==========================================
# DEFAULT CONFIGURATION
# ==========================================
DEFAULT_CHUNK_SIZE = 200
DEFAULT_OVERLAP = 50
DEFAULT_MIN_CONFIDENCE = 0.85
# Reads LLM_API_URL from Docker/Env; falls back to localhost on Windows
DEFAULT_API_URL = os.getenv(
    "LLM_API_URL", 
    "http://localhost:1234/v1/chat/completions"
)
DEFAULT_MODEL_NAME = "qwen2.5-7b-instruct"

# Reads EVAL_RESULTS_DIR from Docker/Env; falls back to relative eval/results
DEFAULT_RESULTS_DIR = Path(
    os.getenv(
        "EVAL_RESULTS_DIR", 
        "eval/results"
    )
)

SYSTEM_PROMPT = (
    "You are a deterministic linguistic normalization and entity-matching engine. Your task is to identify abbreviations, acronyms, and shortened naming handles in Document Text that correspond to the entities provided in Target Canonical List. Extract only verbatim substrings that physically exist in the text. Output strict JSON with no conversational commentary."
)

USER_PROMPT_TEMPLATE = """###Objective
Identify all unique abbreviations, acronyms, and typographical variations in Document Text that derive from any entity in Target Canonical List.

###Allowed Abbreviation Rules
A valid variation must be a direct shorthand, acronym, or exact alias referring to the entity itself, created through one of these mechanisms:
1. Acronyms & Initialisms: Compacting proper nouns into initial letters or syllables.
2. Lexical Shortenings: Standard truncation of individual words (e.g., dropping terminal syllables or using recognized shortened stems).
3. Orthographic Shifts: Variations in capitalization (ALL-CAPS, lowercase), spacing, hyphenation, or punctuation.
4. Delimited Sub-Tokens: When text contains compound strings separated by slashes, hyphens, or periods (e.g., "AAA/BBB/CCC/01"), evaluate each segment independently for potential matches.

Critical Constraints
1. Identity vs. Association: A variation must function as a direct grammatical alias for the canonical entity. Never extract work programs, submission titles, scopes, or contract documents merely because an entity created or worked on them.
2. Contractions Only (No Expansions): A variation must be a strictly shortened, contracted, or abbreviated form. Never extract additive compound phrases, project scopes, facility descriptions, or titles that append extra words to the canonical entity.
3. Syntactic Unit Bounds: A variation must be a compact naming unit (1 to 5 words maximum). Never extract whole clauses, headings, or sentences.
4. Verbatim Match: "variation_found" must appear character-for-character in Document Text. Do not reconstruct or hypothesize unmentioned terms.
5. Deduplication: Extract each unique variation once per canonical entity.
6. Strict Schema: Return ONLY a valid JSON array matching the schema below. No commentary, markdown explanations, or extra fields.

<demonstration>
[Input Canonical List]
- Apex Global Technologies Incorporated
- Northern Valley Water Basin

[Input Document Text]
APEX GLOBAL TECHNOLOGIES INC. ("AGTI") issued reference AGTI/NVWB/2026/001 regarding the Northern Valley Water Basin Rehabilitation Project. Representatives from Apex confirmed that NVWB monitoring stations are active.

[Target Output]
[
{{"canonical_term": "Apex Global Technologies Incorporated", "variation_found": "AGTI"}},
{{"canonical_term": "Apex Global Technologies Incorporated", "variation_found": "Apex"}},
{{"canonical_term": "Northern Valley Water Basin", "variation_found": "NVWB"}}
]
</demonstration>

###Target Canonical List:
{target_canonical_list}

###Document Text:
{document_text}

###Output JSON Format:
[
{{
"canonical_term": "<exact term from Target Canonical List>",
"variation_found": "<exact verbatim substring from Document Text>"
}}
]"""


def load_canonical_from_gazetteer(gazetteer_path: Path) -> Tuple[str, Dict[str, Any]]:
    """Loads existing company and project entities from gazetteer.yaml into prompt format,
    intentionally excluding company_acronyms[cite: 2].
    """
    if not gazetteer_path.exists():
        raise FileNotFoundError(f"Gazetteer file not found at: {gazetteer_path.resolve()}")

    with open(gazetteer_path, "r", encoding="utf-8") as f:
        gaz_data = yaml.safe_load(f) or {}

    companies = gaz_data.get("company_names", []) or []
    projects = gaz_data.get("project_names", []) or []

    lines = []
    if companies:
        lines.append("[ORGANIZATION]")
        for c in companies:
            lines.append(str(c))
    if projects:
        lines.append("[PROJECT_NAME]")
        for p in projects:
            lines.append(str(p))

    return "\n".join(lines), gaz_data


def load_ordered_documents_from_dir(text_dir: Path) -> Dict[str, str]:
    """Scans text_dir for '<stem>.p<n>.txt' page files.
    Groups pages by document stem, sorts them numerically (p1, p2, ..., p10),
    and joins them into a single continuous text string per document[cite: 2].
    """
    if not text_dir.exists():
        print(f"[!] alias_evaluator: Directory not found: {text_dir.resolve()}")
        return {}

    if text_dir.is_file():
        return {text_dir.stem: text_dir.read_text(encoding="utf-8")}

    page_pattern = re.compile(r"^(.*?)\.p(\d+)\.txt$", re.IGNORECASE)
    doc_pages: Dict[str, List[Tuple[int, Path]]] = {}
    fallback_txts: List[Path] = []

    for file_path in text_dir.iterdir():
        if not file_path.is_file():
            continue
        match = page_pattern.match(file_path.name)
        if match:
            stem = match.group(1)
            page_num = int(match.group(2))
            doc_pages.setdefault(stem, []).append((page_num, file_path))
        elif file_path.suffix.lower() == ".txt":
            fallback_txts.append(file_path)

    documents: Dict[str, str] = {}

    for stem, pages in doc_pages.items():
        pages.sort(key=lambda item: item[0])
        full_text = "\n\n".join(
            p[1].read_text(encoding="utf-8") for p in pages
        )
        documents[stem] = full_text

    for txt_file in fallback_txts:
        if txt_file.stem not in documents:
            documents[txt_file.stem] = txt_file.read_text(encoding="utf-8")

    return documents


def create_sliding_window_chunks(text: str, chunk_size: int, overlap: int) -> List[str]:
    """Splits text by words into overlapping chunks[cite: 1, 2]."""
    words = text.split()
    if not words:
        return []
    if overlap >= chunk_size:
        raise ValueError(f"Overlap ({overlap}) must be smaller than Chunk Size ({chunk_size}).")

    stride = chunk_size - overlap
    chunks = []
    for i in range(0, len(words), stride):
        chunk_words = words[i : i + chunk_size]
        chunks.append(" ".join(chunk_words))
        if i + chunk_size >= len(words):
            break
    return chunks


def parse_json_array(text: str) -> List[Dict[str, Any]]:
    """Safely extracts JSON array from model text response[cite: 1, 2]."""
    text_clean = text.strip()
    if text_clean.startswith("```"):
        text_clean = re.sub(r"^```(?:json)?\s*", "", text_clean)
        text_clean = re.sub(r"\s*```$", "", text_clean)

    start_bracket = text_clean.find("[")
    end_bracket = text_clean.rfind("]")
    if start_bracket != -1 and end_bracket != -1 and end_bracket > start_bracket:
        try:
            return json.loads(text_clean[start_bracket : end_bracket + 1])
        except json.JSONDecodeError:
            return []
    return []


def calculate_internal_scores(
    items: List[Dict[str, Any]], raw_response_text: str, logprobs_content: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Calculates geometric mean token confidence: exp(mean(logprob))[cite: 1, 2]."""
    if not logprobs_content:
        return [{**item, "score": None} for item in items]

    token_spans = []
    curr_pos = 0
    for tok_info in logprobs_content:
        tok_str = tok_info.get("token", "")
        logp = tok_info.get("logprob", 0.0)
        start = curr_pos
        end = curr_pos + len(tok_str)
        token_spans.append({"token": tok_str, "start": start, "end": end, "logprob": logp})
        curr_pos = end

    search_offset = 0
    scored_results = []

    for item in items:
        canonical = item.get("canonical_term", "")
        variation = item.get("variation_found", "")
        if not variation:
            continue

        escaped_val = re.escape(variation)
        pattern = rf'"variation_found"\s*:\s*"{escaped_val}"'
        match = re.search(pattern, raw_response_text[search_offset:])

        score = None
        if match:
            abs_start = search_offset + match.start()
            inner_offset = match.group(0).rfind(variation)
            val_start = abs_start + inner_offset
            val_end = val_start + len(variation)
            search_offset = abs_start + match.end()

            matched_logprobs = [
                t["logprob"]
                for t in token_spans
                if max(t["start"], val_start) < min(t["end"], val_end)
            ]
            if matched_logprobs:
                mean_logp = sum(matched_logprobs) / len(matched_logprobs)
                score = round(math.exp(mean_logp), 4)

        scored_results.append({
            "canonical_term": canonical,
            "variation_found": variation,
            "score": score,
        })

    return scored_results


def extract_aliases_from_text(
    document_text: str,
    target_canonical_list: str,
    api_url: str = DEFAULT_API_URL,
    model_name: str = DEFAULT_MODEL_NAME,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> List[Dict[str, Any]]:
    """Runs sliding-window discovery across the document within a single stateful chat session[cite: 2]."""
    snippets = create_sliding_window_chunks(document_text, chunk_size, overlap)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    valid_discoveries: List[Dict[str, Any]] = []

    for idx, snippet in enumerate(snippets, 1):
        user_content = USER_PROMPT_TEMPLATE.format(
            target_canonical_list=target_canonical_list,
            document_text=snippet,
        )
        messages.append({"role": "user", "content": user_content})

        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.0,
            "logprobs": True,
            "top_logprobs": 1,
        }

        try:
            resp = requests.post(api_url, json=payload, timeout=60)
            resp.raise_for_status()
            res_json = resp.json()
        except requests.exceptions.RequestException as e:
            print(f"[!] alias_evaluator: LLM API request failed on snippet {idx}: {e}")
            break

        choice = res_json["choices"][0]
        response_text = choice["message"]["content"]
        logprobs_content = choice.get("logprobs", {}).get("content", [])
        messages.append({"role": "assistant", "content": response_text})

        extracted_items = parse_json_array(response_text)
        scored_items = calculate_internal_scores(extracted_items, response_text, logprobs_content)

        for item in scored_items:
            score = item.get("score")
            if score is not None and score >= min_confidence:
                valid_discoveries.append(item)

    return valid_discoveries


def save_llm_discoveries(
    stem: str,
    discoveries: List[Dict[str, Any]],
    results_dir: Path = DEFAULT_RESULTS_DIR,
) -> Path:
    """Saves the output of the LLM discovery into <stem>.llm_discovery.txt in eval/results."""
    results_dir.mkdir(parents=True, exist_ok=True)
    out_file = results_dir / f"{stem}.llm_discovery.txt"

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(f"=== LLM ALIAS DISCOVERY RESULTS: {stem} ===\n\n")
        if not discoveries:
            f.write("No candidate variations detected meeting confidence threshold.\n")
        else:
            f.write(f"{'CANONICAL TERM':<40} | {'VARIATION DETECTED':<30} | {'CONFIDENCE':<10}\n")
            f.write("-" * 86 + "\n")
            for item in discoveries:
                canonical = item.get("canonical_term", "")
                variation = item.get("variation_found", "")
                score = item.get("score")
                score_str = f"{score:.4f}" if score is not None else "N/A"
                f.write(f"{canonical:<40} | {variation:<30} | {score_str:<10}\n")

            f.write("\n\n=== RAW JSON OUTPUT ===\n")
            f.write(json.dumps(discoveries, indent=2, ensure_ascii=False))

    print(f"[*] Discovery results written to: {out_file.resolve()}")
    return out_file


def update_gazetteer_with_aliases(
    gazetteer_path: Path,
    discoveries: List[Dict[str, Any]],
    gaz_data: Dict[str, Any],
) -> int:
    """Categorizes discoveries and writes updates to gazetteer.yaml with strict

    acronym constraints and case-insensitive deduplication.
    """
    raw_companies = gaz_data.get("company_names", []) or []
    raw_acronyms = gaz_data.get("company_acronyms", []) or []
    raw_projects = gaz_data.get("project_names", []) or []

    # Maintain existing lists
    existing_companies = list(raw_companies)
    existing_acronyms = list(raw_acronyms)
    existing_projects = list(raw_projects)

    # Lowercase lookup sets for case-insensitive deduplication
    lower_companies = {clean_text(str(c)).lower() for c in existing_companies}
    lower_acronyms = {clean_text(str(a)).lower() for a in existing_acronyms}
    lower_projects = {clean_text(str(p)).lower() for p in existing_projects}

    new_additions = 0

    for item in discoveries:
        canonical = item.get("canonical_term", "")
        var = clean_text(item.get("variation_found", ""))
        if not var:
            continue

        var_lower = var.lower()
        canonical_lower = clean_text(canonical).lower()

        # 1. Determine entity type based on canonical match
        is_project = canonical_lower in lower_projects
        is_company = canonical_lower in lower_companies

        if is_project:
            # Case-insensitive deduplication
            if var_lower not in lower_projects:
                existing_projects.append(var)
                lower_projects.add(var_lower)
                new_additions += 1

        elif is_company:
            # Strict Acronym Rule:
            # - Must be a single compact token <= 5 characters (e.g. PCI, GME, AiS)
            # - OR if 2 tokens (e.g. compound acronym like "PCI SB"), each token must be <= 4 chars
            tokens = var.split()
            is_acronym = (len(tokens) == 1 and len(var) <= 5) or (
                len(tokens) == 2 and all(len(t) <= 4 for t in tokens) and var.isupper()
            )

            if is_acronym:
                if var_lower not in lower_acronyms:
                    existing_acronyms.append(var)
                    lower_acronyms.add(var_lower)
                    new_additions += 1
            else:
                if var_lower not in lower_companies:
                    existing_companies.append(var)
                    lower_companies.add(var_lower)
                    new_additions += 1

        else:
            print(f"[!] Warning: Skipping '{var}' (unrecognized canonical '{canonical}').")

    if new_additions > 0:
        gaz_data["company_names"] = sorted(existing_companies, key=lambda s: s.lower())
        gaz_data["company_acronyms"] = sorted(existing_acronyms, key=lambda s: s.lower())
        gaz_data["project_names"] = sorted(existing_projects, key=lambda s: s.lower())

        with open(gazetteer_path, "w", encoding="utf-8") as f:
            yaml.dump(gaz_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        print(f"[*] Added {new_additions} new unique alias(es) to {gazetteer_path.name}.")
    else:
        print("[*] No novel aliases met the threshold to add.")

    return new_additions


def run_alias_discovery(
    gazetteer_path: Path = Path("gazetteer.yaml"),
    text_dir: Path = Path("demo/inputs/text"),
    results_dir: Path = DEFAULT_RESULTS_DIR,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> int:
    """Entrypoint called by recognizer_registry.py on startup:
    Reads base canonicals -> Loads ordered pages -> Runs LLM discovery -> Writes log -> Updates gazetteer.
    """
    canonical_prompt_str, gaz_data = load_canonical_from_gazetteer(gazetteer_path)
    documents = load_ordered_documents_from_dir(text_dir)

    if not documents:
        print(f"[*] No documents found in {text_dir}; skipping alias discovery.")
        return 0

    all_discoveries: List[Dict[str, Any]] = []

    for stem, doc_text in documents.items():
        print(f"[*] Running LLM alias discovery over '{stem}' ({len(doc_text.split())} words)...")
        discoveries = extract_aliases_from_text(
            document_text=doc_text,
            target_canonical_list=canonical_prompt_str,
            min_confidence=min_confidence,
        )
        # Save output file per document stem
        save_llm_discoveries(stem, discoveries, results_dir=results_dir)
        all_discoveries.extend(discoveries)

    added_count = update_gazetteer_with_aliases(
        gazetteer_path=gazetteer_path,
        discoveries=all_discoveries,
        gaz_data=gaz_data,
    )
    return added_count


if __name__ == "__main__":
    g_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("gazetteer.yaml")
    t_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("demo/inputs/text")
    run_alias_discovery(gazetteer_path=g_path, text_dir=t_path)