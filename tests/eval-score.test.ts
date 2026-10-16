// tests/eval-score.test.ts
import { describe, expect, it } from "vitest";
import { scoreDetections } from "../eval/score.js";

describe("scoreDetections", () => {
  it("counts exact matches only; an off-by-one span is both a false positive and a false negative", () => {
    const labels = [
      { type: "PERSON", value: "John Tan", page: 1, start: 18, end: 26 },
      { type: "EMAIL_ADDRESS", value: "a@b.com", page: 1, start: 30, end: 37 },
    ];
    const detections = [
      { type: "PERSON", page: 1, start: 18, end: 26, score: 0.9 },
      { type: "EMAIL_ADDRESS", page: 1, start: 30, end: 36, score: 0.9 },
    ];
    const s = scoreDetections(labels, detections);
    expect(s.truePositives).toBe(1);
    expect(s.falsePositives).toBe(1);
    expect(s.falseNegatives).toBe(1);
    expect(s.leakage).toBe(0); // off-by-one still overlaps, so not a full leak
    expect(s.perType.PERSON?.f1).toBeCloseTo(1);
  });

  it("counts a non-overlapping label as leakage", () => {
    const labels = [
      { type: "PERSON", value: "John Tan", page: 1, start: 18, end: 26 },
    ];
    const detections = [
      { type: "PERSON", page: 1, start: 50, end: 58, score: 0.9 },
    ];
    const s = scoreDetections(labels, detections);
    expect(s.truePositives).toBe(0);
    expect(s.falsePositives).toBe(1);
    expect(s.falseNegatives).toBe(1);
    expect(s.leakage).toBe(1);
  });

  it("dedupes duplicate identical detection spans per type", () => {
    const labels = [
      { type: "PERSON", value: "John Tan", page: 1, start: 18, end: 26 },
    ];
    const detections = [
      { type: "PERSON", page: 1, start: 18, end: 26, score: 0.9 },
      { type: "PERSON", page: 1, start: 18, end: 26, score: 0.9 },
    ];
    const s = scoreDetections(labels, detections);
    expect(s.perType.PERSON?.truePositives).toBe(1);
    expect(s.perType.PERSON?.falsePositives).toBe(0);
  });
});

import { listLeakedSpans } from "../eval/score.js";

it("lists leaked spans for values-file paste", () => {
  const leaked = listLeakedSpans(
    [
      { type: "PERSON", value: "John Tan", page: 1, start: 0, end: 8 },
      { type: "PERSON", value: "Amy Lim", page: 1, start: 20, end: 27 },
    ],
    [{ type: "PERSON", page: 1, start: 0, end: 8, score: 0.9 }]
  );
  expect(leaked).toEqual([{ type: "PERSON", value: "Amy Lim", page: 1, start: 20, end: 27 }]);
});

import { mismatchedSpanKeys, listFalsePositiveSpans, listFalseNegativeSpans, classifyFalsePositive, countFalsePositivesByKind } from "../eval/score.js";

const pageText = (page: number) => (page === 1 ? "John Tan and Amy Lim" : "");

describe("mismatchedSpanKeys / listFalsePositiveSpans / listFalseNegativeSpans", () => {
  it("lists false positive detections and false negative labels with real text", () => {
    const labels = [
      { type: "PERSON", value: "John Tan", page: 1, start: 0, end: 8 },
      { type: "PERSON", value: "Amy Lim", page: 1, start: 13, end: 20 },
    ];
    const detections = [
      { type: "PERSON", page: 1, start: 0, end: 8, score: 0.9 }, // true positive
      { type: "PERSON", page: 2, start: 0, end: 5, score: 0.9 }, // false positive
    ];
    const keys = mismatchedSpanKeys(labels, detections);
    expect([...keys.falsePositiveKeys]).toEqual(["PERSON 2 0 5"]);
    expect([...keys.falseNegativeKeys]).toEqual(["PERSON 1 13 20"]);

    const falsePositives = listFalsePositiveSpans(labels, detections, pageText);
    expect(falsePositives).toEqual([
      { type: "PERSON", page: 2, start: 0, end: 5, score: 0.9, text: "", kind: "no-label", label: undefined },
    ]);

    const falseNegatives = listFalseNegativeSpans(labels, detections, pageText);
    expect(falseNegatives).toEqual([{ type: "PERSON", page: 1, start: 13, end: 20, text: "Amy Lim" }]);
  });

  it("counts match false positive and false negative detail lengths", () => {
    const labels = [
      { type: "PERSON", value: "A", page: 1, start: 0, end: 1 },
      { type: "PERSON", value: "B", page: 1, start: 2, end: 3 },
    ];
    const detections = [
      { type: "PERSON", page: 1, start: 0, end: 1, score: 0.9 },
      { type: "EMAIL", page: 1, start: 5, end: 9, score: 0.9 },
    ];
    const s = scoreDetections(labels, detections);
    expect(listFalsePositiveSpans(labels, detections, pageText)).toHaveLength(s.falsePositives);
    expect(listFalseNegativeSpans(labels, detections, pageText)).toHaveLength(s.falseNegatives);
  });

  it("dedupes duplicate identical false positive detections", () => {
    const labels = [{ type: "PERSON", value: "A", page: 1, start: 0, end: 1 }];
    const detections = [
      { type: "EMAIL", page: 1, start: 5, end: 9, score: 0.9 },
      { type: "EMAIL", page: 1, start: 5, end: 9, score: 0.8 },
    ];
    const falsePositives = listFalsePositiveSpans(labels, detections, pageText);
    expect(falsePositives).toHaveLength(1);
  });

  it("classifies same-span different-type as wrong-type", () => {
    const labels = [{ type: "PERSON", value: "John Tan", page: 1, start: 0, end: 8 }];
    const detections = [{ type: "ORGANIZATION", page: 1, start: 0, end: 8, score: 0.9 }];
    const falsePositives = listFalsePositiveSpans(labels, detections, pageText);
    expect(falsePositives).toHaveLength(1);
    expect(falsePositives[0]?.kind).toBe("wrong-type");
    expect(falsePositives[0]?.label).toMatchObject({ type: "PERSON", start: 0, end: 8 });
  });

  it("classifies same-type overlap with shifted offsets as boundary", () => {
    const labels = [{ type: "EMAIL_ADDRESS", value: "a@b.com", page: 1, start: 30, end: 37 }];
    const fullText = (page: number) => (page === 1 ? "x".repeat(30) + "a@b.com" : "");
    const detections = [{ type: "EMAIL_ADDRESS", page: 1, start: 30, end: 36, score: 0.9 }];
    const falsePositives = listFalsePositiveSpans(labels, detections, fullText);
    expect(falsePositives).toHaveLength(1);
    expect(falsePositives[0]?.kind).toBe("boundary");
    expect(falsePositives[0]?.label).toMatchObject({ type: "EMAIL_ADDRESS", start: 30, end: 37 });
  });

  it("classifies a detection with no overlapping label as no-label", () => {
    const labels = [{ type: "PERSON", value: "John Tan", page: 1, start: 0, end: 8 }];
    const detections = [{ type: "EMAIL", page: 1, start: 13, end: 20, score: 0.9 }];
    const { kind, label } = classifyFalsePositive(detections[0]!, labels, pageText);
    expect(kind).toBe("no-label");
    expect(label).toBeUndefined();
  });

  it("counts false positives by kind", () => {
    const labels = [
      { type: "PERSON", value: "John Tan", page: 1, start: 0, end: 8 },
      { type: "PERSON", value: "Amy Lim", page: 1, start: 13, end: 20 },
    ];
    const detections = [
      { type: "ORGANIZATION", page: 1, start: 0, end: 8, score: 0.9 }, // wrong-type
      { type: "PERSON", page: 1, start: 13, end: 19, score: 0.9 }, // boundary
      { type: "EMAIL", page: 2, start: 0, end: 5, score: 0.9 }, // no-label (other page)
    ];
    expect(countFalsePositivesByKind(labels, detections, pageText)).toEqual({
      "wrong-type": 1,
      boundary: 1,
      "no-label": 1,
    });
  });
});
