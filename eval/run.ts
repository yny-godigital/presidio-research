// eval/run.ts — WHAT: runs detection plus roundtrip scoring over labeled PDFs.
// WHY: this is the single entry point that turns labels into comparable scores.
// INPUT: PDFs in --inputs plus labels in --labels. OUTPUT: eval/results/summary.json.
// EXAMPLE: npx tsx eval/run.ts --only pilot_a --out eval/results/summary.json
import { readdirSync, readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { join, basename } from "node:path";
import { parseArg, parseOnlySet } from "./cli.js";
import { normalizeStem } from "./normalize.js";
import { validateLabel, type LabeledEntity, type LabelFile } from "./schema.js";
import { extractPdfPages, EXTRACTOR_ID } from "./extract.js";
import {
  listFalseNegativeSpans,
  listFalsePositiveSpans,
  listLeakedSpans,
  scoreDetections,
  type DetectionScore,
  type FalsePositiveBreakdown,
  type FalsePositiveSpan,
  type ScoredSpan,
  type SpanWithText,
} from "./score.js";
import { scoreRoundtrip, type RoundtripPage, type RoundtripScore } from "./roundtrip.js";
import { PresidioAnalyzerClient } from "./analyzer-client.js";
import colors from "picocolors";
const { bold, cyan, dim, green, red, yellow } = colors;

export function checkPairing(pdfs: string[], labelFiles: string[]): string[] {
  const pdfStems = pdfs.map((f) => normalizeStem(f));
  const pdfStemSet = new Set(pdfStems);
  const labelStems = labelFiles.map((f) => normalizeStem(f.replace(/\.json$/i, "")));
  const labelStemSet = new Set(labelStems);
  const missingLabels = pdfStems.filter((s) => !labelStemSet.has(s));
  const extraLabels = labelStems.filter((s) => !pdfStemSet.has(s));
  if (missingLabels.length > 0 || extraLabels.length > 0) {
    throw new Error(`Label pairing mismatch: missing labels for PDF stems: [${missingLabels.join(", ")}]; extra labels with no PDF: [${extraLabels.join(", ")}]`);
  }
  return pdfStems;
}

// Everything the runner produces for one file: the summary row plus the detail
// that only the console and the detection-detail file use.
export type ScoredFile = {
  file: string;
  stem: string;
  detection: DetectionScore;
  leaked: LabeledEntity[];
  falsePositives: FalsePositiveSpan[];
  falseNegatives: SpanWithText[];
  falsePositiveBreakdown: FalsePositiveBreakdown;
  roundtrip: Omit<RoundtripScore, "pages">;
  roundtripPages: RoundtripPage[];
};

export async function scoreOnePdf(
  pdfPath: string,
  labelPath: string,
  client: PresidioAnalyzerClient,
): Promise<ScoredFile> {
  const file = basename(pdfPath);
  const stem = normalizeStem(file);
  const label = JSON.parse(readFileSync(labelPath, "utf8")) as LabelFile;
  if (label.extractor !== EXTRACTOR_ID) throw new Error(`Extractor mismatch for ${file}: label has ${label.extractor}, runner uses ${EXTRACTOR_ID}`);
  const pages = await extractPdfPages(pdfPath);
  validateLabel(pages, label);
  const detections: ScoredSpan[] = [];
  for (let page = 1; page <= pages.length; page++) {
    const found = await client.analyze(pages[page - 1] ?? "");
    for (const d of found) detections.push({ type: d.entityType, page, start: d.start, end: d.end, score: d.score });
  }
  const detection = scoreDetections(label.entities, detections);
  const leaked = listLeakedSpans(label.entities, detections);
  const pageText = (page: number) => pages[page - 1] ?? "";
  const falsePositives = listFalsePositiveSpans(label.entities, detections, pageText);
  const falseNegatives = listFalseNegativeSpans(label.entities, detections, pageText);
  const falsePositiveBreakdown: FalsePositiveBreakdown = { "wrong-type": 0, boundary: 0, "no-label": 0 };
  for (const f of falsePositives) falsePositiveBreakdown[f.kind]++;
  const roundtrip = await scoreRoundtrip(pages, client);
  const { pages: roundtripPages, ...roundtripScore } = roundtrip;
  return { file, stem, detection, leaked, falsePositives, falseNegatives, falsePositiveBreakdown, roundtrip: roundtripScore, roundtripPages };
}

export function excerpt(text: string, limit = 200): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > limit ? `${flat.slice(0, limit)}...` : flat;
}

async function main(): Promise<void> {
  const inputsDir = parseArg("inputs", "demo/inputs");
  const labelsDir = parseArg("labels", "demo/inputs/labels");
  const analyzerUrl = parseArg("analyzer", process.env.PRESIDIO_ANALYZER_URL ?? "http://127.0.0.1:3000");
  const outPath = parseArg("out", "eval/results/summary.json");
  // --only stem1,stem2 scores a subset (e.g. freshly labeled pilots); without
  // it every PDF must have exactly one label and vice versa.
  const only = parseOnlySet(parseArg("only", ""));

  const client = new PresidioAnalyzerClient(analyzerUrl);

  const pdfs = readdirSync(inputsDir)
    .filter((f) => f.toLowerCase().endsWith(".pdf"))
    .filter((f) => only.size === 0 || only.has(normalizeStem(f)));
  const labelFiles = readdirSync(labelsDir)
    .filter((f) => !f.startsWith(".") && f.toLowerCase().endsWith(".json"))
    .filter((f) => only.size === 0 || only.has(normalizeStem(f.replace(/\.json$/i, ""))));
  checkPairing(pdfs, labelFiles);
  const results: unknown[] = [];
  const resultsDir = join(outPath, "..");
  for (const pdf of pdfs) {
    const stem = normalizeStem(pdf);
    const row = await scoreOnePdf(join(inputsDir, pdf), join(labelsDir, `${stem}.json`), client);
    const { roundtripPages, falsePositives, falseNegatives, ...summaryRow } = row;
    results.push(summaryRow);
    writeDetailFiles(resultsDir, stem, roundtripPages);
    writeDetectionDetailFile(resultsDir, stem, falsePositives, falseNegatives);
    printScoreLine(row);
    printPageExcerpts(roundtripPages);
    printLeakedValues(row);
    printFalsePositives(row);
    printFalseNegatives(row);
  }
  mkdirSync(resultsDir, { recursive: true });
  writeFileSync(outPath, JSON.stringify({ analyzerUrl, extractor: EXTRACTOR_ID, results }, null, 2));
}

/** One console line per file, colored by threshold. */
function printScoreLine(row: ScoredFile): void {
  const f1Str = row.detection.f1.toFixed(3);
  const recallStr = row.detection.recall.toFixed(3);
  const restoreRateStr = row.roundtrip.restoreRate.toFixed(3);
  const leakStr = String(row.detection.leakage);
  const { falsePositiveBreakdown } = row;
  const falsePositiveStr = `${row.falsePositives.length} (wrong-type=${falsePositiveBreakdown["wrong-type"]} boundary=${falsePositiveBreakdown.boundary} no-label=${falsePositiveBreakdown["no-label"]})`;
  const falseNegativeStr = String(row.falseNegatives.length);
  const scoreColor = (v: number) => (v >= 0.8 ? green : v >= 0.5 ? yellow : red);
  const restoreRateColor = (v: number) => (v >= 0.95 ? green : v >= 0.8 ? yellow : red);
  const countColor = (n: number) => (n === 0 ? green : red);
  console.log(
    `${bold(cyan(row.stem))}: F1=${scoreColor(row.detection.f1)(f1Str)} recall=${scoreColor(row.detection.recall)(recallStr)} leakage=${countColor(row.detection.leakage)(leakStr)} restoreRate=${restoreRateColor(row.roundtrip.restoreRate)(restoreRateStr)} falsePositives=${countColor(row.falsePositives.length)(falsePositiveStr)} falseNegatives=${countColor(row.falseNegatives.length)(falseNegativeStr)}`
  );
}

/** The first non-empty page, once, as an anonymized and restored preview. */
function printPageExcerpts(roundtripPages: RoundtripPage[]): void {
  const first = roundtripPages.find((p) => !p.skipped);
  if (!first) return;
  console.log(`  ${dim("anon:")} ${excerpt(first.anonymized)}`);
  console.log(`  ${dim("restored:")} ${excerpt(first.restored)}`);
}

function printLeakedValues(row: ScoredFile): void {
  if (row.leaked.length === 0) return;
  console.log(`${red(`leaked values for ${row.stem} (paste into ${row.stem}.values.txt after review):`)}`);
  for (const l of row.leaked) console.log(`  ${red(l.type)}: ${l.value}`);
}

function printFalsePositives(row: ScoredFile): void {
  if (row.falsePositives.length === 0) return;
  console.log(`${yellow(`false positives for ${row.stem}:`)}`);
  for (const f of row.falsePositives) console.log(`  ${yellow(`[${f.kind}] ${f.type}`)} p${f.page} ${f.start}..${f.end}: ${excerpt(f.text)}${labelHint(f)}`);
}

function printFalseNegatives(row: ScoredFile): void {
  if (row.falseNegatives.length === 0) return;
  console.log(`${yellow(`false negatives for ${row.stem}:`)}`);
  for (const f of row.falseNegatives) console.log(`  ${yellow(f.type)} p${f.page} ${f.start}..${f.end}: ${excerpt(f.text)}`);
}

function labelHint(f: FalsePositiveSpan): string {
  if (!f.label) return "";
  return ` (label was ${f.label.type} ${f.label.start}..${f.label.end}: ${excerpt(f.label.text, 80)})`;
}

function writeDetailFiles(dir: string, stem: string, pages: RoundtripPage[]): void {
  mkdirSync(dir, { recursive: true });
  const anonSections = pages.map((p) => {
    const header = `--- page ${p.page} ---`;
    if (p.skipped) return `${header}\n(empty page, skipped)`;
    if (p.blocked.length > 0) return `${header}\nblocked: ${p.blocked.join(", ")}`;
    return `${header}\n// mapping: ${JSON.stringify(p.mapping)}\n${p.anonymized}`;
  });
  const restoredSections = pages.map((p) => {
    const header = `--- page ${p.page} ---`;
    if (p.skipped) return `${header}\n(empty page, skipped)`;
    if (p.blocked.length > 0) return `${header}\nblocked: ${p.blocked.join(", ")}`;
    return `${header}\n${p.restored}`;
  });
  writeFileSync(join(dir, `${stem}.anonymized.txt`), `${anonSections.join("\n\n")}\n`);
  writeFileSync(join(dir, `${stem}.restored.txt`), `${restoredSections.join("\n\n")}\n`);
}

function writeDetectionDetailFile(dir: string, stem: string, falsePositives: FalsePositiveSpan[], falseNegatives: SpanWithText[]): void {
  mkdirSync(dir, { recursive: true });
  const lines: string[] = [];
  lines.push(`--- false positives (${falsePositives.length}) ---`);
  for (const kind of ["wrong-type", "boundary", "no-label"] as const) {
    const group = falsePositives.filter((f) => f.kind === kind);
    if (group.length === 0) continue;
    lines.push(`--- false positive: ${kind} (${group.length}) ---`);
    for (const f of group) {
      lines.push(`page ${f.page}: ${f.type} @ ${f.start}..${f.end}`);
      lines.push(`  ${JSON.stringify(f.text)}`);
      if (f.label) {
        lines.push(`  label was ${f.label.type} @ ${f.label.start}..${f.label.end}`);
        lines.push(`  ${JSON.stringify(f.label.text)}`);
      }
    }
  }
  lines.push(`--- false negatives (${falseNegatives.length}) ---`);
  for (const f of falseNegatives) {
    lines.push(`page ${f.page}: ${f.type} @ ${f.start}..${f.end}`);
    lines.push(`  ${JSON.stringify(f.text)}`);
  }
  lines.push("");
  writeFileSync(join(dir, `${stem}.detection-detail.txt`), `${lines.join("\n")}\n`);
}

// The guard keeps the module importable without side effects, so a future test
// can import the exported helpers. `tsx eval/run.ts` still runs the full script.
const invokedAsCli = (process.argv[1] ?? "").endsWith("run.ts");
if (invokedAsCli) {
  await main();
}
