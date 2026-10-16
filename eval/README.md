# What the eval is
The tools in eval/ score two things on every PDF page.
Detection scoring compares service output with your labels and reports precision, recall, F1, and leakage. Leakage counts labels that no detection of the same type overlaps on the same page. It is the privacy measure, and it should stay at 0.
Roundtrip scoring ignores labels. It sends each page through /anonymize and then /restore and checks the page returns byte-identical, reported as restoreRate.
Both scores go into one row per file in eval/results/summary.json.

# How to run it
From the repo root:
just setup                    # one-time: .env, npm install, uv sync
just compose-up               # start the analyzer, or: just presidio-serve
just wait-presidio            # poll /health until it answers

just eval                     # score the whole corpus
just eval-only stem1,stem2    # score a subset, skip the full pairing check
just demo                     # compose up, wait, then eval

The console prints one line per file:
<stem>: F1=0.917 recall=0.956 leakage=0 restoreRate=1.000 falsePositives=2 (wrong-type=1 boundary=1 no-label=0) falseNegatives=1
The run also writes per-file detail next to summary.json: every false positive and false negative with the real text in <stem>.detection-detail.txt, plus the full page texts in <stem>.anonymized.txt and <stem>.restored.txt.
Why it works this way
Files pair by stem. demo/inputs/A-3.0 - Sub.pdf matches demo/inputs/labels/a_3_0_sub.json after lowercasing and replacing non-alphanumerics with single underscores. Without --only, the run exits with an error if any PDF lacks a label or any label lacks a PDF, so a stale corpus cannot pass unnoticed.
Exact match means type, page, start, and end all agree. An off-by-one boundary produces both a false positive and a false negative, so F1 measures span accuracy rather than whether the service saw something.
F1 aggregates exact-match precision and recall, so it drops when boundaries are off. Leakage counts labels with no same-type overlapping detection at all, so a boundary miss is not a leak. The two numbers together answer different questions: how clean the spans are, and whether any value escaped.
Roundtrip scoring needs no labels, which lets it catch tokenization or mapping errors in /anonymize and /restore that detection scores would miss.
The false-positive breakdown splits mismatches into wrong-type, boundary, and no-label, which points to the failing part: recognizer scoring, boundary logic, or a detection with no matching label.

Labeling new files
1. Put the PDF in demo/inputs/.
2. Run just prelabel --only <stem> to draft labels from service output, then fix them by hand.
3. Or write labels/<stem>.values.txt with one TYPE: value line per entity and run just resolve-labels --only <stem> to convert values into exact offsets.
4. Run just eval-only <stem>, read detection-detail.txt, and adjust labels or recognizers until the two agree where they should.

# Eval

Scores Presidio detection against labeled PDFs and checks that the live service
can anonymize a page and restore it byte-identical.

## How one run flows

```
demo/inputs/<stem>.pdf
  -> eval/extract.ts        one text string per page (PyMuPDF words, OCR fallback)
  -> POST /analyze          detections per page
  -> eval/score.ts          exact-match scoring plus overlap leakage
  -> POST /anonymize, /restore   roundtrip scoring per page
  -> eval/results/          summary.json plus per-file detail files
```

Detection scoring compares the service output with the human labels. Roundtrip
scoring ignores the labels. It sends each page through `/anonymize`, sends the
result through `/restore`, and checks whether the page returns byte-identical.
Both scores land in the same summary row.

## Glossary

| Term | Meaning |
| --- | --- |
| stem | The file name without the extension, lowercased with single underscores. `A-3.0 - Sub.pdf` becomes `a_3_0_sub`. PDFs and labels pair by stem. |
| label | A human-confirmed PII span in `demo/inputs/labels/<stem>.json`: type, page, start, end, and the exact text `value`. |
| detection | A span the service returned from `POST /analyze` for one page. |
| exact match | A detection and a label agree on type, page, start, and end. Spans compare by the key `TYPE page start end`. |
| truePositives | Detections with an exact match. |
| falsePositives | Detections without an exact match. |
| falseNegatives | Labels without an exact match, either missed or returned with the wrong type or boundaries. |
| precision | Share of detections that matched: `truePositives / (truePositives + falsePositives)`. |
| recall | Share of labels that were found: `truePositives / (truePositives + falseNegatives)`. |
| F1 | One number between 0 and 1 that balances precision and recall. |
| leakage | Labels that no detection of the same type overlaps on the same page. This is the privacy measure. Lower is better. A boundary miss is not a leak. |
| roundtrip, restoreRate | Share of tokens on pages that returned byte-identical from `/anonymize` and `/restore`. |
| wrong-type | False-positive kind. The detection matches a label exactly but the type differs. |
| boundary | False-positive kind. The detection has the right type and overlaps a label, but the offsets differ. |
| no-label | False-positive kind. No label overlaps the detection. |

## Worked example: one off-by-one span

A label marks `PERSON` at 18..26. The service returns `PERSON` at 18..25, one
character short.

- Exact match: no. The offsets differ, so the run adds one `falsePositives`
  and one `falseNegatives`. Precision and recall each drop.
- Leakage: no. The detection overlaps the label, so the value counts as caught.

This is why a run reports both an F1 score and a leakage count. F1 tells you
how clean the spans are. Leakage tells you whether any value escaped.

## Usage

1. Put PDFs in `demo/inputs/`.
2. Extract text with `extractPdfPages()` (PyMuPDF 1.28.2 word pipeline, per-page
   tesseract OCR below 20 native chars). `extractPdfWords` exposes the boxes.
3. Build labels in `demo/inputs/labels/<stem>.json`, either with
   `npx tsx eval/prelabel.ts` (Presidio draft, human must fix) or with
   `npx tsx eval/resolve-labels.ts` (`TYPE: value` lines in
   `labels/<stem>.values.txt`).
4. Run `npx tsx eval/run.ts --only <stem>` to write `eval/results/summary.json`.

Useful flags: `--inputs`, `--labels`, `--analyzer`, `--out`, and `--only`.
`--only stem1,stem2` scores a subset. Without it, every PDF must have exactly
one label and every label one PDF.

## Reading the output

The console prints one line per file:

```
<stem>: F1=0.917 recall=0.956 leakage=0 restoreRate=1.000 falsePositives=2 (wrong-type=1 boundary=1 no-label=0) falseNegatives=1
```

Colors mark the thresholds. F1 and recall turn green at 0.8 and up.
restoreRate turns green at 0.95 and up. Counts turn green at 0. Below the line,
the first non-empty page prints an `anon:` and a `restored:` excerpt.

`eval/results/summary.json` holds one row per file: `file`, `stem`, `detection`
(`truePositives`, `falsePositives`, `falseNegatives`, `precision`, `recall`,
`f1`, `leakage`, `perType`), `leaked`, `falsePositiveBreakdown`, and `roundtrip`
(`totalTokens`, `restoredTokens`, `restoreRate`).

Per-stem detail files:

- `<stem>.anonymized.txt` and `<stem>.restored.txt` hold the full page texts
  after `/anonymize` and after `/restore`.
- `<stem>.detection-detail.txt` lists every false positive and false negative
  with the real text, so you can see what the service got wrong. The console
  echoes the list.
