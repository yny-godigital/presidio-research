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
  listTruePositiveSpans,
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

//---everything the runner produces for one file: the summary row plus the detail that only the console and the detection-detail file use.
export type ScoredFile = {
  file: string;
  stem: string;
  detection: DetectionScore;
  leaked: LabeledEntity[];
  truePositives: SpanWithText[];
  falsePositives: FalsePositiveSpan[];
  falseNegatives: SpanWithText[];
  falsePositiveBreakdown: FalsePositiveBreakdown;
  rawDetections: (SpanWithText & { score: number })[];
  roundtrip: Omit<RoundtripScore, "pages">;
  roundtripPages: RoundtripPage[];
  presidioArtifactPages: string[];
  rawNlpPages: string[];
};
const ANALYZER_URL = process.env.PRESIDIO_ANALYZER_URL || "http://127.0.0.1:3000";
//---normalize then compare pdf and label, throwing errors when a pair is missing a part 
//stems are file name without the extension, lowercased with single underscores.
export function checkPairing(pdfs: string[], labelFiles: string[]): string[] {
  // 1. Normalize the pdf files into stems
  const pdfStems = pdfs.map((f) => normalizeStem(f)); 
  const pdfStemSet = new Set(pdfStems);

  // 2. Normalize the label files into stems
  const labelStems = labelFiles.map((f) => normalizeStem(f.replace(/\.json$/i, "")));
  const labelStemSet = new Set(labelStems);

  // 3. Check for missing pairs between pdf and label files
  const missingLabels = pdfStems.filter((s) => !labelStemSet.has(s));
  const extraLabels = labelStems.filter((s) => !pdfStemSet.has(s));
  if (missingLabels.length > 0 || extraLabels.length > 0) {
    throw new Error(`Label pairing mismatch: missing labels for PDF stems: [${missingLabels.join(", ")}]; extra labels with no PDF: [${extraLabels.join(", ")}]`);
  }
  return pdfStems;
}

//---evaluate recognizer outputby comparing it with a complete answer key (label); also where the api is called
export async function scoreOnePdf(pdfPath: string, labelPath: string, client: PresidioAnalyzerClient): Promise<ScoredFile> {
  const file = basename(pdfPath);
  const stem = normalizeStem(file);
  const label = JSON.parse(readFileSync(labelPath, "utf8")) as LabelFile;

  if (label.extractor !== EXTRACTOR_ID) throw new Error(`Extractor mismatch for ${file}: label has ${label.extractor}, runner uses ${EXTRACTOR_ID}`);

  // 1. extract raw text from pdf file
  const pages = await extractPdfPages(pdfPath);

  // 1.1 verify extracted text and label text are the same
  validateLabel(pages, label);

  const detections: ScoredSpan[] = [];
  const rawDetections: (SpanWithText & { score: number })[] = [];
  const presidioArtifactPages: string[] = [];
  const rawNlpPages: string[] = [];

  // 2. send the extracted text to the analyzer per page, then collect the returned information
  for (let page = 1; page <= pages.length; page++) {
    const pageStr = pages[page - 1] ?? "";
    // */analyze is called here*
    const { resolved, raw, presidioArtifacts, rawNlp } = await client.analyzeWithRaw(pageStr, page);

    // 2.1 store the detections
    for (const d of resolved) {
      detections.push({
        type: d.entityType,
        page,
        start: d.start,
        end: d.end,
        score: d.score,
        origin: d.origin ?? "unknown",
      });
    }

    for (const r of raw) {
      rawDetections.push({
        type: r.entityType,
        page,
        start: r.start,
        end: r.end,
        score: r.score,
        origin: r.origin ?? "unknown",
        text: pageStr.slice(r.start, r.end),
      });
    }

    if (presidioArtifacts) {
      presidioArtifactPages.push(presidioArtifacts);
    }
    if (rawNlp) {
      rawNlpPages.push(rawNlp);
    }
  }

  // 2.2 calculate the metrics, and find the correctly and incorrectly detected information
  const detection = scoreDetections(label.entities, detections);
  const leaked = listLeakedSpans(label.entities, detections);
  const pageText = (page: number) => pages[page - 1] ?? "";
  const truePositives = listTruePositiveSpans(label.entities, detections, pageText);
  const falsePositives = listFalsePositiveSpans(label.entities, detections, pageText);
  const falseNegatives = listFalseNegativeSpans(label.entities, detections, pageText);
  const falsePositiveBreakdown: FalsePositiveBreakdown = { "wrong-type": 0, boundary: 0, "no-label": 0 };

  for (const f of falsePositives) falsePositiveBreakdown[f.kind]++;

  // 3. evaluates the anonymize and restore cycle
  // */anonymize is called here*
  const roundtrip = await scoreRoundtrip(pages, client);
  const { pages: roundtripPages, ...roundtripScore } = roundtrip;

  return { file, stem, detection, leaked, truePositives, falsePositives, falseNegatives, falsePositiveBreakdown, rawDetections, roundtrip: roundtripScore, roundtripPages, presidioArtifactPages, rawNlpPages};
}

//---cut a string if its too long
export function excerpt(text: string, limit = 200): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > limit ? `${flat.slice(0, limit)}...` : flat;
}

async function syncAndReloadAnalyzer(): Promise<void> {
  const response = await fetch(`${ANALYZER_URL}/reload`, { method: "POST" });
  if (!response.ok) {
    throw new Error(`Failed to reload analyzer: ${response.status} ${response.statusText}`);
  }
}

//---main orchestrator of the process
async function main(): Promise<void> {
  // await syncAndReloadAnalyzer();
  
  // 0. available commands
  const inputsDir = parseArg("inputs", "demo/inputs");
  const labelsDir = parseArg("labels", "demo/inputs/labels");
  const analyzerUrl = parseArg("analyzer", process.env.PRESIDIO_ANALYZER_URL ?? "http://127.0.0.1:3000");
  const outPath = parseArg("out", "eval/results/summary.json");
  // --only stem1,stem2 scores a subset (e.g. freshly labeled pilots); without
  // it every PDF must have exactly one label and vice versa.
  const only = parseOnlySet(parseArg("only", ""));

  const client = new PresidioAnalyzerClient(analyzerUrl);

  // 1. collect files and validate their pairs
  // 1.1 collect and filter pdf files
  const pdfs = readdirSync(inputsDir)
    .filter((f) => f.toLowerCase().endsWith(".pdf"))
    .filter((f) => only.size === 0 || only.has(normalizeStem(f)));

  // 1.2 collect and filter label files
  const labelFiles = readdirSync(labelsDir)
    .filter((f) => !f.startsWith(".") && f.toLowerCase().endsWith(".json"))
    .filter((f) => only.size === 0 || only.has(normalizeStem(f.replace(/\.json$/i, ""))));

  // 1.3 verify pairing
  checkPairing(pdfs, labelFiles);

  // 2. call the recognizer/anonymizer and save/write its outputs
  const results: unknown[] = [];
  const resultsDir = join(outPath, "..");

  // 2.1 runs on all pdfs
  for (const pdf of pdfs) {
    // 2.2 identify and evaluate the document
    const stem = normalizeStem(pdf);
    const row = await scoreOnePdf(join(inputsDir, pdf), join(labelsDir, `${stem}.json`), client);

    // 2.3 separate summary metrics from unneeded details 
    const { roundtripPages, truePositives, falsePositives, falseNegatives, presidioArtifactPages, rawNlpPages, ...summaryRow } = row;

    results.push(summaryRow);

    // 2.4 save artifacts except for summary.json
    writeDetailFiles(resultsDir, stem, roundtripPages);
    writeDetectionDetailFile(resultsDir, stem, truePositives, falsePositives, falseNegatives);
    writeRawResultsFile(resultsDir, stem, row.rawDetections);
    writeArtifactFiles(resultsDir, stem, presidioArtifactPages, rawNlpPages);

    // 2.5 write into terminal
    printScoreLine(row);
    printPageExcerpts(roundtripPages);
    printLeakedValues(row);
    printFalsePositives(row);
    printFalseNegatives(row);
  }

  // 2.6 create and save summary.json
  mkdirSync(resultsDir, { recursive: true });
  writeFileSync(outPath, JSON.stringify({ analyzerUrl, extractor: EXTRACTOR_ID, results }, null, 2));
}

//---writing into the console
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

//---writing to files and its specifications
function writeDetailFiles(dir: string, stem: string, pages: RoundtripPage[]): void {
  mkdirSync(dir, { recursive: true });

  const anonSections = pages.map((p) => {
    const header = `--- page ${p.page} ---`;
    if (p.skipped) return `${header}\n(empty page, skipped)`;
    if (p.blocked.length > 0) return `${header}\nblocked: ${p.blocked.join(", ")}`;
    return `${header}\n${p.anonymized}`;
  });

  const restoredSections = pages.map((p) => {
    const header = `--- page ${p.page} ---`;
    if (p.skipped) return `${header}\n(empty page, skipped)`;
    if (p.blocked.length > 0) return `${header}\nblocked: ${p.blocked.join(", ")}`;
    return `${header}\n${p.restored}`;
  });

  const mappingSections = pages.map((p) => {
    const header = `--- page ${p.page} ---`;
    if (p.skipped) return `${header}\n(empty page, skipped)`;
    if (!p.mapping || p.mapping.length === 0) return `${header}\n(no tokens)`;

    const tokenLines = p.mapping.map((m) => {
      const orig = JSON.stringify(m.original);
      return `  ${m.token.padEnd(24)} | ${m.entity_type.padEnd(16)} -> ${orig}`;
    });

    return `${header}\n${tokenLines.join("\n")}`;
  });

  writeFileSync(join(dir, `${stem}.anonymized.txt`), `${anonSections.join("\n\n")}\n`);
  writeFileSync(join(dir, `${stem}.restored.txt`), `${restoredSections.join("\n\n")}\n`);
  writeFileSync(join(dir, `${stem}.mapping.txt`), `${mappingSections.join("\n\n")}\n`);
}

function writeRawResultsFile(dir: string, stem: string, rawDetections: (SpanWithText & { score: number })[]): void {
  mkdirSync(dir, { recursive: true });
  const lines: string[] = [];

  // Group by page
  const maxPage = rawDetections.reduce((max, d) => Math.max(max, d.page), 1);

  for (let page = 1; page <= maxPage; page++) {
    const pageItems = rawDetections.filter((d) => d.page === page);
    lines.push(`--- page ${page} (${pageItems.length} raw detections) ---`);
    if (pageItems.length === 0) {
      lines.push("  (no detections)");
      continue;
    }

    for (const item of pageItems) {
      lines.push(
        `page ${item.page}: ${item.type} @ ${item.start}..${item.end} [via ${item.origin ?? "unknown"}]`
      );
      lines.push(`  ${JSON.stringify(item.text)}`);
    }
    lines.push("");
  }

  writeFileSync(join(dir, `${stem}.raw-results.txt`), `${lines.join("\n")}\n`);
}

function writeArtifactFiles(
  dir: string,
  stem: string,
  presidioArtifactPages: string[],
  rawNlpPages: string[]
): void {
  mkdirSync(dir, { recursive: true });

  if (presidioArtifactPages.length > 0) {
    writeFileSync(
      join(dir, `${stem}.PresidioNlpArtifacts`),
      `${presidioArtifactPages.join("\n\n").trimEnd()}\n`,
      "utf-8"
    );
  }

  if (rawNlpPages.length > 0) {
    const rawNlpHeader = `NLP Engine: spaCy (en_core_web_sm)\n\n`;
    writeFileSync(
      join(dir, `${stem}.rawNlp`),
      `${rawNlpHeader}${rawNlpPages.join("\n\n").trimEnd()}\n`,
      "utf-8"
    );
  }
}

interface FormattedRow {
  leftType: string;
  leftSpan: string;
  leftOrigin: string;
  leftText: string;
  rightType?: string;
  rightSpan?: string;
  rightOrigin?: string;
  rightText?: string;
}

function formatPageRows(rows: FormattedRow[]): string[] {
  let maxLeftType = 0;
  let maxLeftSpan = 0;
  let maxLeftOrigin = 0;
  let maxLeftText = 0;
  const hasRight = rows.some((r) => r.rightType !== undefined);

  let maxRightType = 0;
  let maxRightSpan = 0;
  let maxRightOrigin = 0;

  for (const r of rows) {
    if (r.leftType.length > maxLeftType) maxLeftType = r.leftType.length;
    if (r.leftSpan.length > maxLeftSpan) maxLeftSpan = r.leftSpan.length;
    if (r.leftOrigin.length > maxLeftOrigin) maxLeftOrigin = r.leftOrigin.length;
    if (hasRight && r.leftText.length > maxLeftText) maxLeftText = r.leftText.length;

    if (r.rightType !== undefined) {
      if (r.rightType.length > maxRightType) maxRightType = r.rightType.length;
      const spanLen = (r.rightSpan ?? "").length;
      if (spanLen > maxRightSpan) maxRightSpan = spanLen;
      const originLen = (r.rightOrigin ?? "").length;
      if (originLen > maxRightOrigin) maxRightOrigin = originLen;
    }
  }

  return rows.map((r) => {
    const c0 = r.leftType.padEnd(maxLeftType);
    const c1 = r.leftSpan.padEnd(maxLeftSpan);
    const c2 = r.leftOrigin.padEnd(maxLeftOrigin);

    if (hasRight && r.rightType !== undefined) {
      const c3 = r.leftText.padEnd(maxLeftText);
      const r0 = r.rightType.padEnd(maxRightType);
      const r1 = (r.rightSpan ?? "").padEnd(maxRightSpan);
      const r2 = (r.rightOrigin ?? "").padEnd(maxRightOrigin);
      const r3 = r.rightText ?? "";
      return `${c0} | ${c1} | ${c2} | ${c3} || SUPPOSEDLY || ${r0} | ${r1} | ${r2} | ${r3}`;
    }

    return `${c0} | ${c1} | ${c2} | ${r.leftText}`;
  });
}

function groupByPage<T extends { page: number }>(items: T[]): Map<number, T[]> {
  const map = new Map<number, T[]>();
  for (const item of items) {
    const list = map.get(item.page) ?? [];
    list.push(item);
    map.set(item.page, list);
  }
  return map;
}

function writeDetectionDetailFile(
  dir: string,
  stem: string,
  truePositives: SpanWithText[],
  falsePositives: FalsePositiveSpan[],
  falseNegatives: SpanWithText[]
): void {
  mkdirSync(dir, { recursive: true });
  const lines: string[] = [];

  const formatText = (text: string) => text.replace(/\r?\n/g, "\\n");

  // --- FALSE POSITIVES ---
  lines.push(`--- false positive (${falsePositives.length}) ---`);
  for (const kind of ["wrong-type", "boundary", "no-label"] as const) {
    const group = falsePositives.filter((f) => f.kind === kind);
    if (group.length === 0) continue;

    lines.push(` -- false positive: ${kind} (${group.length}) --`);
    const pages = groupByPage(group);

    for (const [page, items] of pages.entries()) {
      lines.push(`  - page ${page} -`);
      const rows: FormattedRow[] = items.map((f) => {
        const row: FormattedRow = {
          leftType: f.type,
          leftSpan: `${f.start}-${f.end}`,
          leftOrigin: f.origin ?? "unknown",
          leftText: formatText(f.text),
        };
        if (f.label) {
          row.rightType = f.label.type;
          row.rightSpan = `${f.label.start}-${f.label.end}`;
          row.rightOrigin = (f.label as any).origin ?? "ground_truth";
          row.rightText = formatText(f.label.text);
        }
        return row;
      });

      lines.push(...formatPageRows(rows));
      lines.push("");
    }
  }

  // --- FALSE NEGATIVES ---
  if (falseNegatives.length > 0) {
    lines.push(`--- false negatives (${falseNegatives.length}) ---`);
    const pages = groupByPage(falseNegatives);

    for (const [page, items] of pages.entries()) {
      lines.push(`  - page ${page} -`);
      const rows: FormattedRow[] = items.map((f) => ({
        leftType: f.type,
        leftSpan: `${f.start}-${f.end}`,
        leftOrigin: f.origin ?? "ground_truth",
        leftText: formatText(f.text),
      }));

      lines.push(...formatPageRows(rows));
      lines.push("");
    }
  }

  // === TRUE POSITIVES ===
  lines.push(`=== TRUE POSITIVES (${truePositives.length}) ===`);
  const pages = groupByPage(truePositives);

  for (const [page, items] of pages.entries()) {
    lines.push(`  = page ${page} =`);
    const rows: FormattedRow[] = items.map((t) => ({
      leftType: t.type,
      leftSpan: `${t.start}-${t.end}`,
      leftOrigin: t.origin ?? "unknown",
      leftText: formatText(t.text),
    }));

    lines.push(...formatPageRows(rows));
    lines.push("");
  }

  writeFileSync(join(dir, `${stem}.detection-detail.txt`), `${lines.join("\n").trimEnd()}\n`);
}

// The guard keeps the module importable without side effects, so a future test
// can import the exported helpers. `tsx eval/run.ts` still runs the full script.
const invokedAsCli = (process.argv[1] ?? "").endsWith("run.ts");
if (invokedAsCli) {
  await main();
}
