#!/usr/bin/env python3
# eval/extract_pdf.py — WHAT: extracts per-page text plus word boxes as JSON.
# WHY: detection offsets and redaction boxes must come from one word list.
# INPUT: one PDF path on argv. OUTPUT: {"pages": [{"text", "words": [{t,x0,y0,x1,y1,ocr}]}]} on stdout.
# EXAMPLE: python3 eval/extract_pdf.py demo/inputs/x.pdf
"""Word-level PDF extraction with PyMuPDF and a per-page OCR fallback. OCR boxes are mapped from 300-DPI TSV pixels back to PDF points."""
import json
import sys

try:
    import pymupdf as fitz  # PyMuPDF 1.28.2; the `fitz` shim prints its deprecation notice to stdout, corrupting the JSON contract.
except ImportError:
    print("ERROR: missing PyMuPDF. Install it with: pip install pymupdf==1.28.2", file=sys.stderr)
    sys.exit(2)

NATIVE_CHAR_FLOOR = 20
OCR_DPI = 300


def warn(msg):
    # Every diagnostic goes to stderr; stdout carries only JSON.
    print("extract_pdf.py warning: " + msg, file=sys.stderr)


def assemble_text(scored_words):
    # Sort by (block, line, word_no); group lines; join words with one
    # space and lines with a newline, so blocks end newline-separated.
    ordered = sorted(scored_words, key=lambda w: (w["block"], w["line"], w["word_no"]))
    lines = []
    for w in ordered:
        if not (w["t"] or "").strip():
            continue
        if lines and lines[-1][0] == (w["block"], w["line"]):
            lines[-1][1].append(w)
        else:
            lines.append(((w["block"], w["line"]), [w]))
    records = []
    texts = []
    for _, group in lines:
        texts.append(" ".join(g["t"] for g in group))
        for g in group:
            records.append({"t": g["t"], "x0": g["x0"], "y0": g["y0"],
                            "x1": g["x1"], "y1": g["y1"], "ocr": g["ocr"]})
    return "\n".join(texts), records


def native_page_words(page):
    # get_text("words") rows: (x0, y0, x1, y1, text, block_no, line_no, word_no).
    raw = page.get_text("words")
    return [
        {"t": t, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
         "block": b, "line": ln, "word_no": wn, "ocr": False}
        for (x0, y0, x1, y1, t, b, ln, wn) in raw
    ]


def ocr_page_words(page):
    # Render one page at 300 DPI, run `tesseract stdin TSV`, map each
    # surviving word box back to PDF points, and flag every word ocr=True.
    import shutil
    import subprocess
    tesseract = shutil.which("tesseract")
    if tesseract is None:
        print("ERROR: tesseract not found. Install it with: sudo apt install tesseract-ocr",
              file=sys.stderr)
        sys.exit(2)
    zoom = OCR_DPI / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    proc = subprocess.run(
        [tesseract, "stdin", "stdout", "--psm", "6", "-c", "tessedit_create_tsv=1"],
        input=bytes(pix.tobytes("png")), capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError("tesseract failed: " + proc.stderr.decode("utf-8", "replace")[-500:])
    lines = proc.stdout.decode("utf-8", "replace").splitlines()
    if not lines:
        return []
    header = lines[0].split("\t")
    idx = {name: pos for pos, name in enumerate(header)}
    need = ["level", "page_num", "block_num", "par_num", "line_num",
            "word_num", "left", "top", "width", "height", "conf", "text"]
    if any(k not in idx for k in need):
        raise RuntimeError("unexpected tesseract TSV header: " + lines[0][:120])
    words = []
    for row in lines[1:]:
        cols = row.split("\t")
        if len(cols) <= idx["text"]:
            continue
        if cols[idx["level"]] != "5":
            continue
        text = cols[idx["text"]].strip()
        try:
            conf = float(cols[idx["conf"]])
        except ValueError:
            conf = -1
        if not text or conf < 0:
            continue
        # TSV pixels become PDF points by dividing out the 300-DPI zoom.
        x0 = int(cols[idx["left"]]) / zoom
        y0 = int(cols[idx["top"]]) / zoom
        words.append({"t": text, "x0": x0, "y0": y0,
                      "x1": x0 + int(cols[idx["width"]]) / zoom,
                      "y1": y0 + int(cols[idx["height"]]) / zoom,
                      "block": (int(cols[idx["block_num"]]), int(cols[idx["par_num"]]),
                                int(cols[idx["page_num"]])),
                      "line": (int(cols[idx["line_num"]])),
                      "word_no": int(cols[idx["word_num"]]), "ocr": True})
    return words


def extract_page(page, pdf_path, index):
    try:
        native_text = page.get_text() or ""
        if len(native_text.strip()) >= NATIVE_CHAR_FLOOR:
            return assemble_text(native_page_words(page))
        try:
            return assemble_text(ocr_page_words(page))
        except NotImplementedError:
            warn("page %d of %s has < %d native chars; OCR pending"
                 % (index + 1, pdf_path, NATIVE_CHAR_FLOOR))
            return "", []
    except Exception as exc:
        warn("page %d of %s failed (%s); yielding empty text" % (index + 1, pdf_path, exc))
        return "", []


def main(argv):
    if len(argv) != 2:
        print("usage: python3 eval/extract_pdf.py <pdfPath>", file=sys.stderr)
        return 2
    pdf_path = argv[1]
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        print("ERROR: cannot open %s: %s" % (pdf_path, exc), file=sys.stderr)
        return 1
    if doc.page_count == 0:
        print("ERROR: %s has zero pages" % pdf_path, file=sys.stderr)
        return 1
    pages = []
    for i in range(doc.page_count):
        text, words = extract_page(doc[i], pdf_path, i)
        pages.append({"text": text, "words": words})
    json.dump({"pages": pages}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
