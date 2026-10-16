// eval/resolve-labels.ts — WHAT: turns a human value list into label offsets.
// WHY: a human lists TYPE plus value lines; the tool finds every occurrence.
// INPUT: labels/<stem>.values.txt with TYPE: value lines. OUTPUT: labels/<stem>.json.
// EXAMPLE: npx tsx eval/resolve-labels.ts --only pilot_a --force
// Overlapping values keep the longest span, matching the scorer.
// Matching is case-insensitive with whitespace runs collapsed (PDF extraction
// spacing varies), but recorded offsets stay exact into the extracted text.
import { readdirSync, readFileSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join, basename } from "node:path";
import { parseArg, parseOnlySet } from "./cli.js";
import { keepLongestNonOverlapping } from "./spans.js";
import { normalizeStem } from "./normalize.js";
import { validateLabel, type LabelFile, type LabeledEntity } from "./schema.js";
import { extractPdfPages, EXTRACTOR_ID } from "./extract.js";

export const arg = parseArg;

export function parseValues(raw: string): { type: string; value: string }[] {
  const out: { type: string; value: string }[] = [];
  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const colon = trimmed.indexOf(":");
    if (colon < 0) throw new Error(`Bad values line (want "TYPE: value"): ${JSON.stringify(line)}`);
    const type = trimmed.slice(0, colon).trim();
    const value = trimmed.slice(colon + 1).trim();
    if (!type || !value) throw new Error(`Bad values line (empty type or value): ${JSON.stringify(line)}`);
    out.push({ type, value });
  }
  return out;
}

export function normalizeWithMap(text: string): { normalized: string; indexMap: number[] } {
  // Collapse every whitespace run to one space and case-fold for search.
  // indexMap[n] is the original offset of normalized[n], so hits map back
  // to exact original spans (offsets must stay exact for validateLabel).
  // Example: "John  Tan" becomes "john tan" with indexMap [0,1,2,3,4,6,7,8].
  const chars: string[] = [];
  const indexMap: number[] = [];
  let prevWasSpace = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i] ?? "";
    if (/\s/.test(ch)) {
      if (!prevWasSpace) {
        chars.push(" ");
        indexMap.push(i);
      }
      prevWasSpace = true;
    } else {
      chars.push(ch.toLowerCase());
      indexMap.push(i);
      prevWasSpace = false;
    }
  }
  return { normalized: chars.join(""), indexMap };
}

export function normalizeValue(value: string): string {
  return value.replace(/\s+/g, " ").trim().toLowerCase();
}

export function allOccurrences(text: string, value: string): { start: number; end: number }[] {
  // Normalized search (case + whitespace), exact original offsets.
  const needle = normalizeValue(value);
  const spans: { start: number; end: number }[] = [];
  if (!needle) return spans;
  const { normalized, indexMap } = normalizeWithMap(text);
  let from = 0;
  while (true) {
    const ns = normalized.indexOf(needle, from);
    if (ns < 0) break;
    spans.push({ start: indexMap[ns] ?? 0, end: (indexMap[ns + needle.length - 1] ?? 0) + 1 });
    from = ns + 1;
  }
  return spans;
}

export function resolveAllOccurrences(
  pages: string[],
  wanted: { type: string; value: string }[]
): { entities: LabeledEntity[]; misses: string[]; multiSurface: { query: string; hits: number; surfaces: string[] }[] } {
  const entities: LabeledEntity[] = [];
  const misses: string[] = [];
  const multiSurface: { query: string; hits: number; surfaces: string[] }[] = [];
  for (const { type, value } of wanted) {
    let hits = 0;
    const surfaces = new Set<string>();
    pages.forEach((text, i) => {
      for (const { start, end } of allOccurrences(text, value)) {
        // Store the true slice, not the human's query string: the doc's
        // casing/spacing is what validateLabel checks the offsets against.
        const slice = text.slice(start, end);
        entities.push({ type, value: slice, page: i + 1, start, end });
        surfaces.add(slice);
        hits++;
      }
    });
    if (hits === 0) {
      misses.push(`${type}: ${value}`);
    } else if (surfaces.size > 1) {
      multiSurface.push({ query: `${type}: ${value}`, hits, surfaces: [...surfaces] });
    }
  }
  return { entities, misses, multiSurface };
}

export async function resolveLabels(options: {
  inputsDir: string;
  labelsDir: string;
  textDir: string;
  only: Set<string>;
  allowMissing: boolean;
  force: boolean;
}): Promise<boolean> {
  const { inputsDir, labelsDir, textDir, only, allowMissing, force } = options;
  mkdirSync(labelsDir, { recursive: true });
  mkdirSync(textDir, { recursive: true });
  const pdfs = readdirSync(inputsDir)
    .filter((f) => f.toLowerCase().endsWith(".pdf"))
    .filter((f) => only.size === 0 || only.has(normalizeStem(f)));

  let failed = false;
  for (const pdf of pdfs) {
    const stem = normalizeStem(pdf);
    const valuesPath = join(labelsDir, `${stem}.values.txt`);
    const outPath = join(labelsDir, `${stem}.json`);
    if (!existsSync(valuesPath)) {
      console.log(`${stem}: no values file, skipping (create ${basename(valuesPath)})`);
      continue;
    }
    if (existsSync(outPath) && !force) {
      console.log(`${stem}: label JSON exists, skipping (use --force to overwrite)`);
      continue;
    }
    const pages = await extractPdfPages(join(inputsDir, pdf));
    pages.forEach((t, i) => writeFileSync(join(textDir, `${stem}.p${i + 1}.txt`), t));
    const wanted = parseValues(readFileSync(valuesPath, "utf8"));
    const { entities, misses, multiSurface } = resolveAllOccurrences(pages, wanted);
    if (misses.length > 0) {
      console.log(`${stem}: MISS (${misses.length} values not found in extracted text):`);
      for (const m of misses) console.log(`  - ${m}`);
      if (!allowMissing) {
        failed = true;
        continue;
      }
    }
    if (multiSurface.length > 0) {
      console.log(`${stem}: ${multiSurface.length} values matched multiple surface forms:`);
      for (const m of multiSurface.slice(0, 10))
        console.log(`  - ${m.query} -> ${m.hits} hits: ${m.surfaces.map((s) => JSON.stringify(s)).join(" | ")}`);
    }
    // Longest span wins on overlap; identical to the prelabel dedupe rule.
    const { kept, dropped } = keepLongestNonOverlapping(entities);
    if (dropped.length > 0) {
      console.log(`${stem}: dropped ${dropped.length} overlapping shorter spans:`);
      for (const d of dropped.slice(0, 10)) console.log(`  - p${d.page} [${d.start}-${d.end}] ${d.type} ${d.value}`);
    }
    const label: LabelFile = { source: pdf, extractor: EXTRACTOR_ID, entities: kept };
    validateLabel(pages, label);
    writeFileSync(outPath, JSON.stringify(label, null, 2) + "\n");
    console.log(`${stem}: ${kept.length} entities from ${wanted.length} values`);
  }
  return !failed;
}

async function main(): Promise<void> {
  const ok = await resolveLabels({
    inputsDir: parseArg("inputs", "demo/inputs"),
    labelsDir: parseArg("labels", "demo/inputs/labels"),
    textDir: parseArg("text", "demo/inputs/text"),
    only: parseOnlySet(parseArg("only", "")),
    allowMissing: process.argv.includes("--allow-missing"),
    force: process.argv.includes("--force"),
  });
  if (!ok) {
    console.error("resolve-labels: some values files had misses; fix them or pass --allow-missing");
    process.exit(1);
  }
}

const invokedAsCli = (process.argv[1] ?? "").endsWith("resolve-labels.ts");
if (invokedAsCli) {
  await main();
}
