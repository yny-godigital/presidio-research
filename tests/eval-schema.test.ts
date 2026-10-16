// tests/eval-schema.test.ts
import { describe, expect, it } from "vitest";
import { validateLabel, type LabelFile } from "../eval/schema.js";

const pages = ["Draft an email to John Tan."];

describe("validateLabel", () => {
  it("accepts a correct slice", () => {
    const label: LabelFile = {
      source: "a.pdf",
      extractor: "pdfjs-dist-4.2.67",
      entities: [{ type: "PERSON", value: "John Tan", page: 1, start: 18, end: 26 }],
    };
    expect(() => validateLabel(pages, label)).not.toThrow();
  });

  it("rejects page out of range, bad offsets, and slice mismatch", () => {
    const badPage: LabelFile = {
      source: "a.pdf",
      extractor: "pdfjs-dist-4.2.67",
      entities: [{ type: "PERSON", value: "John Tan", page: 2, start: 0, end: 8 }],
    };
    expect(() => validateLabel(pages, badPage)).toThrow(/page/);
    const badSlice: LabelFile = {
      source: "a.pdf",
      extractor: "pdfjs-dist-4.2.67",
      entities: [{ type: "PERSON", value: "John Tan", page: 1, start: 0, end: 8 }],
    };
    expect(() => validateLabel(pages, badSlice)).toThrow(/slice mismatch/);
  });
});
