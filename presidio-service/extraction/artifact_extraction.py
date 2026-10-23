"""In-process extractor and uniform pipe-delimited formatter for NLP artifacts.

Captures both Presidio's NlpArtifacts and raw spaCy Doc attributes.
"""

from pathlib import Path
from typing import Any, List, Optional


def _escape_newlines(val: Any) -> str:
    """Ensure multiline tokens stay on a single line."""
    if val is None:
        return ""
    return str(val).replace("\r", "").replace("\n", "\\n")


def _format_uniform_table(headers: List[str], rows: List[List[str]]) -> str:
    """Pad columns dynamically to the maximum character width of the current page."""
    if not rows:
        return " | ".join(headers)

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if len(cell) > col_widths[i]:
                col_widths[i] = len(cell)

    header_line = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    body_lines = [
        " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row))
        for row in rows
    ]
    return "\n".join([header_line] + body_lines)


def format_presidio_artifacts_page(
    nlp_artifacts: Any,
    page: Optional[int] = None,
) -> str:
    """Format Presidio NlpArtifacts tokens, lemmas, POS, and entities into a uniform table."""
    headers = ["TOKEN", "SPAN", "LEMMA", "POS", "SPACY ENTITY"]
    rows: List[List[str]] = []

    tokens = getattr(nlp_artifacts, "tokens", []) or []
    indices = getattr(nlp_artifacts, "tokens_indices", []) or []
    lemmas = getattr(nlp_artifacts, "lemmas", []) or []
    pos_tags = getattr(nlp_artifacts, "pos_tags", []) or []
    entities = getattr(nlp_artifacts, "entities", []) or []

    for i, tok in enumerate(tokens):
        tok_str = str(tok) if tok is not None else ""
        span_str = ""
        tok_start: Optional[int] = None
        tok_end: Optional[int] = None

        if i < len(indices):
            raw_idx = indices[i]
            # Handle both list of single start ints [0, 7] and list of tuples [(0, 6)]
            if isinstance(raw_idx, (tuple, list)) and len(raw_idx) >= 2:
                tok_start = int(raw_idx[0])
                tok_end = int(raw_idx[1])
            elif isinstance(raw_idx, int):
                tok_start = raw_idx
                tok_end = tok_start + len(tok_str)

            if tok_start is not None and tok_end is not None:
                span_str = f"{tok_start}-{tok_end}"

        lemma = lemmas[i] if i < len(lemmas) else ""
        pos = pos_tags[i] if i < len(pos_tags) else ""

        # Map token to underlying spaCy entity prediction if contained within span
        ent_label = "-"
        if tok_start is not None and tok_end is not None:
            for ent in entities:
                ent_start = getattr(ent, "start_char", getattr(ent, "start", None))
                ent_end = getattr(ent, "end_char", getattr(ent, "end", None))
                label = getattr(ent, "label_", getattr(ent, "entity_type", None))
                if ent_start is not None and ent_end is not None and label:
                    if tok_start >= ent_start and tok_end <= ent_end:
                        ent_label = str(label)
                        break

        rows.append([
            _escape_newlines(tok_str),
            span_str,
            _escape_newlines(lemma),
            _escape_newlines(pos),
            ent_label,
        ])

    table = _format_uniform_table(headers, rows)
    if page is not None:
        return f"--- page {page} ---\n{table}"
    return table


def format_raw_spacy_page(
    doc: Any,
    page: Optional[int] = None,
) -> str:
    """Format raw spaCy Doc linguistic tokens, tags, syntax trees, and IOB labels."""
    headers = ["TOKEN", "SPAN", "LEMMA", "POS", "TAG", "DEP", "HEAD", "ENTITY"]
    rows: List[List[str]] = []

    for tok in doc:
        span_str = f"{tok.idx}-{tok.idx + len(tok)}"
        ent_tag = f"{tok.ent_iob_}-{tok.ent_type_}" if tok.ent_type_ else "O"

        rows.append([
            _escape_newlines(tok.text),
            span_str,
            _escape_newlines(tok.lemma_),
            _escape_newlines(tok.pos_),
            _escape_newlines(tok.tag_),
            _escape_newlines(tok.dep_),
            _escape_newlines(tok.head.text),
            ent_tag,
        ])

    table = _format_uniform_table(headers, rows)
    if page is not None:
        return f"--- page {page} ---\n{table}"
    return table


def write_artifact_files(
    output_dir: Path | str,
    stem: str,
    presidio_pages: List[str],
    raw_nlp_pages: List[str],
    nlp_model_name: str = "en_core_web_sm",
) -> None:
    """Write <stem>.PresidioNlpArtifacts and <stem>.rawNlp with standard headers."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # 1. Presidio NLP Artifacts
    presidio_content = "\n\n".join(presidio_pages).strip() + "\n"
    (out_path / f"{stem}.PresidioNlpArtifacts").write_text(
        presidio_content, encoding="utf-8"
    )

    # 2. Raw spaCy NLP Output
    raw_header = f"NLP Engine: spaCy ({nlp_model_name})\n\n"
    raw_content = raw_header + "\n\n".join(raw_nlp_pages).strip() + "\n"
    (out_path / f"{stem}.rawNlp").write_text(raw_content, encoding="utf-8")