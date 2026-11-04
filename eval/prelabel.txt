// eval/prelabel.ts — WHAT: drafts candidate labels from Presidio detections.
// WHY: a human reviews every candidate and adds what Presidio missed.
// INPUT: PDFs in --inputs. OUTPUT: labels/<stem>.json drafts for review.
// EXAMPLE: npx tsx eval/prelabel.ts --only pilot_a --min-score 0.35
// WARNING: every candidate is a draft. Fix types and boundaries by hand.
import { readdirSync, readFileSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { parseArg, parseOnlySet } from "./cli.js";
import { normalizeStem } from "./normalize.js";
import { validateLabel, type LabelFile, type LabeledEntity } from "./schema.js";
import { extractPdfPages, EXTRACTOR_ID } from "./extract.js";
import { keepLongestNonOverlapping } from "./spans.js";
import { PresidioAnalyzerClient } from "./analyzer-client.js";

const inputsDir = parseArg("inputs", "demo/inputs");
const labelsDir = parseArg("labels", "demo/inputs/labels");
const analyzerUrl = parseArg("analyzer", process.env.PRESIDIO_ANALYZER_URL ?? "http://127.0.0.1:3000");
const only = parseOnlySet(parseArg("only", ""));
// Skip low-confidence detections; the reviewer still adds what Presidio missed.
const minScore = Number(parseArg("min-score", "0.35"));
const force = process.argv.includes("--force");

const client = new PresidioAnalyzerClient(analyzerUrl);

mkdirSync(labelsDir, { recursive: true });
const pdfs = readdirSync(inputsDir)
  .filter((f) => f.toLowerCase().endsWith(".pdf"))
  .filter((f) => only.size === 0 || only.has(normalizeStem(f)));

for (const pdf of pdfs) {
  const stem = normalizeStem(pdf);
  const outPath = join(labelsDir, `${stem}.json`);
  if (existsSync(outPath) && !force) {
    console.log(`${stem}: label exists, skipping (use --force to overwrite)`);
    continue;
  }
  const pages = await extractPdfPages(join(inputsDir, pdf));
  const entities: LabeledEntity[] = [];
  for (let page = 1; page <= pages.length; page++) {
    const text = pages[page - 1] ?? "";
    const candidates = (await client.analyze(text)).filter((d) => d.score >= minScore);
    // Longest span wins on overlap; the score breaks ties for identical spans.
    const { kept } = keepLongestNonOverlapping(candidates.map((d) => ({ ...d, page })));
    for (const d of kept) {
      entities.push({ type: d.entityType, value: text.slice(d.start, d.end), page, start: d.start, end: d.end });
    }
  }
  const label: LabelFile = { source: pdf, extractor: EXTRACTOR_ID, entities };
  validateLabel(pages, label);
  writeFileSync(outPath, JSON.stringify(label, null, 2) + "\n");
  const counts = new Map<string, number>();
  for (const e of entities) counts.set(e.type, (counts.get(e.type) ?? 0) + 1);
  console.log(
    `${stem}: ${pages.length} pages, ${entities.length} candidates ` +
      [...counts.entries()].map(([t, n]) => `${t}=${n}`).join(" ")
  );
}
