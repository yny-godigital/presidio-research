// eval/score.ts — WHAT: scores Presidio detections against human labels.
// WHY: exact match measures span quality; leakage measures privacy risk. The two
//   disagree by design: a label at 18..26 with a detection at 18..25 is one false
//   positive and one false negative under exact match, and zero leakage, because
//   any overlap means the value was caught.
// INPUT: labeled entities plus detected spans. OUTPUT: DetectionScore.
// EXAMPLE: scoreDetections(labels, detections) -> { truePositives: 1, falsePositives: 0, falseNegatives: 0, f1: 1, leakage: 0 }
import type { LabeledEntity } from "./schema.js";
import { spansOverlap } from "./spans.js";

/** One detection returned by the analyzer for one page. */
export type ScoredSpan = {
  type: string;
  page: number;
  start: number;
  end: number;
  score: number;
};

/** Counts plus precision, recall, and F1 for one entity type. */
export type TypeMetrics = {
  truePositives: number;
  falsePositives: number;
  falseNegatives: number;
  precision: number;
  recall: number;
  f1: number;
};

export type DetectionScore = {
  truePositives: number;
  falsePositives: number;
  falseNegatives: number;
  precision: number;
  recall: number;
  f1: number;
  leakage: number;
  perType: Record<string, TypeMetrics>;
};

function precisionRecallF1(truePositives: number, falsePositives: number, falseNegatives: number): { precision: number; recall: number; f1: number } {
  const precision = truePositives + falsePositives === 0 ? 1 : truePositives / (truePositives + falsePositives);
  const recall = truePositives + falseNegatives === 0 ? 1 : truePositives / (truePositives + falseNegatives);
  const f1 = precision + recall === 0 ? 0 : (2 * precision * recall) / (precision + recall);
  return { precision, recall, f1 };
}

/** Exact-match key for one span. Two spans compare equal only when type, page, and offsets all match. */
export function makeSpanKey(s: { type: string; page: number; start: number; end: number }): string {
  return `${s.type} ${s.page} ${s.start} ${s.end}`;
}

/** The label and detection key sets, shared by every exact-match count in this file. */
function spanKeySets(
  labels: LabeledEntity[],
  detections: ScoredSpan[]
): { labelKeys: Set<string>; detectionKeys: Set<string> } {
  return {
    labelKeys: new Set(labels.map(makeSpanKey)),
    detectionKeys: new Set(detections.map(makeSpanKey)),
  };
}

/** Counts exact matches over deduped detection keys, then derives the misses on both sides. */
function countKeyMatches(
  labelKeys: Set<string>,
  detectionKeys: Set<string>
): { truePositives: number; falsePositives: number; falseNegatives: number } {
  let truePositives = 0;
  for (const k of detectionKeys) if (labelKeys.has(k)) truePositives++;
  return { truePositives, falsePositives: detectionKeys.size - truePositives, falseNegatives: labelKeys.size - truePositives };
}

export function countExactMatches(
  labels: LabeledEntity[],
  detections: ScoredSpan[]
): { truePositives: number; falsePositives: number; falseNegatives: number } {
  const { labelKeys, detectionKeys } = spanKeySets(labels, detections);
  return countKeyMatches(labelKeys, detectionKeys);
}

export function listLeakedSpans(labels: LabeledEntity[], detections: ScoredSpan[]): LabeledEntity[] {
  // Leakage is the privacy measure: a label counts as caught when any detection
  // of the same type on the same page overlaps it, even when offsets differ.
  return labels.filter(
    (l) => !detections.some((d) => d.type === l.type && d.page === l.page && spansOverlap(d, l))
  );
}

export function countLeakage(labels: LabeledEntity[], detections: ScoredSpan[]): number {
  return listLeakedSpans(labels, detections).length;
}

export function scoreDetections(labels: LabeledEntity[], detections: ScoredSpan[]): DetectionScore {
  const { truePositives, falsePositives, falseNegatives } = countExactMatches(labels, detections);
  const leakage = countLeakage(labels, detections);
  const perType: DetectionScore["perType"] = {};
  for (const t of new Set([...labels.map((l) => l.type), ...detections.map((d) => d.type)])) {
    const labelKeys = new Set(labels.filter((l) => l.type === t).map(makeSpanKey));
    const detectionKeys = new Set(detections.filter((d) => d.type === t).map(makeSpanKey));
    const counts = countKeyMatches(labelKeys, detectionKeys);
    perType[t] = { ...counts, ...precisionRecallF1(counts.truePositives, counts.falsePositives, counts.falseNegatives) };
  }
  return { truePositives, falsePositives, falseNegatives, ...precisionRecallF1(truePositives, falsePositives, falseNegatives), leakage, perType };
}

// FP/FN detail: which exact spans are false positives / false negatives,
// with the real text attached via a page-text callback (kept pure + testable).
/** A span plus the text it covers, for display in the console and detail files. */
export type SpanWithText = {
  type: string;
  page: number;
  start: number;
  end: number;
  text: string;
};

// WHY three FP kinds: exact-match scoring lumps "wrong label" together with
// "no label at all", but the fix differs (type mapping vs threshold/gazetteer).
export type FalsePositiveKind = "wrong-type" | "boundary" | "no-label";

/** A false positive with its classified kind and the label it collided with, if any. */
export type FalsePositiveSpan = SpanWithText & {
  score: number;
  kind: FalsePositiveKind;
  /** Overlapping label on the same page, if any — the ground truth this FP collided with. */
  label?: SpanWithText;
};

export type FalsePositiveBreakdown = Record<FalsePositiveKind, number>;

function overlapLength(a: { start: number; end: number }, b: { start: number; end: number }): number {
  return Math.max(0, Math.min(a.end, b.end) - Math.max(a.start, b.start));
}

export function classifyFalsePositive(
  detection: ScoredSpan,
  labels: LabeledEntity[],
  pageText: (page: number) => string
): { kind: FalsePositiveKind; label?: SpanWithText } {
  const overlapping = labels.filter((l) => l.page === detection.page && spansOverlap(detection, l));
  if (overlapping.length === 0) return { kind: "no-label" };
  const toSpan = (l: LabeledEntity): SpanWithText => ({
    type: l.type,
    page: l.page,
    start: l.start,
    end: l.end,
    text: spanText(pageText, l),
  });
  // Same offsets, different type: the detector found the right span with the wrong name.
  const exactTypeMismatch = overlapping.find(
    (l) => l.start === detection.start && l.end === detection.end && l.type !== detection.type
  );
  if (exactTypeMismatch) return { kind: "wrong-type", label: toSpan(exactTypeMismatch) };
  // Same type overlapping with different offsets: right entity, wrong boundary.
  const sameType = overlapping.filter((l) => l.type === detection.type);
  const pool = sameType.length > 0 ? sameType : overlapping;
  let best = pool[0]!;
  for (const l of pool) {
    if (overlapLength(detection, l) > overlapLength(detection, best)) best = l;
  }
  return {
    kind: sameType.length > 0 ? "boundary" : "wrong-type",
    label: toSpan(best),
  };
}

export function countFalsePositivesByKind(
  labels: LabeledEntity[],
  detections: ScoredSpan[],
  pageText: (page: number) => string
): FalsePositiveBreakdown {
  const out: FalsePositiveBreakdown = { "wrong-type": 0, boundary: 0, "no-label": 0 };
  for (const f of listFalsePositiveSpans(labels, detections, pageText)) out[f.kind]++;
  return out;
}

/** Exact-match keys split into the false positives and the false negatives. */
export type MismatchedSpanKeys = { falsePositiveKeys: Set<string>; falseNegativeKeys: Set<string> };

export function mismatchedSpanKeys(
  labels: LabeledEntity[],
  detections: ScoredSpan[]
): MismatchedSpanKeys {
  const { labelKeys, detectionKeys } = spanKeySets(labels, detections);
  return {
    falsePositiveKeys: new Set([...detectionKeys].filter((k) => !labelKeys.has(k))),
    falseNegativeKeys: new Set([...labelKeys].filter((k) => !detectionKeys.has(k))),
  };
}

function spanText(pageText: (page: number) => string, s: { page: number; start: number; end: number }): string {
  return pageText(s.page).slice(s.start, s.end);
}

export function listFalsePositiveSpans(
  labels: LabeledEntity[],
  detections: ScoredSpan[],
  pageText: (page: number) => string
): FalsePositiveSpan[] {
  const { falsePositiveKeys } = mismatchedSpanKeys(labels, detections);
  const seen = new Set<string>();
  const out: FalsePositiveSpan[] = [];
  for (const d of detections) {
    const k = makeSpanKey(d);
    if (falsePositiveKeys.has(k) && !seen.has(k)) {
      seen.add(k);
      const { kind, label } = classifyFalsePositive(d, labels, pageText);
      out.push({ ...d, text: spanText(pageText, d), kind, label });
    }
  }
  return out;
}

export function listFalseNegativeSpans(
  labels: LabeledEntity[],
  detections: ScoredSpan[],
  pageText: (page: number) => string
): SpanWithText[] {
  const { falseNegativeKeys } = mismatchedSpanKeys(labels, detections);
  const seen = new Set<string>();
  const out: SpanWithText[] = [];
  for (const l of labels) {
    const k = makeSpanKey(l);
    if (falseNegativeKeys.has(k) && !seen.has(k)) {
      seen.add(k);
      out.push({ type: l.type, page: l.page, start: l.start, end: l.end, text: spanText(pageText, l) });
    }
  }
  return out;
}
