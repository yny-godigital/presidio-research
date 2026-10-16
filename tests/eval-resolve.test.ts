// tests/eval-resolve.test.ts
import { describe, expect, it } from "vitest";
import { parseValues, allOccurrences, resolveAllOccurrences } from "../eval/resolve-labels.js";

describe("parseValues", () => {
  it("parses TYPE: value lines, skipping blanks and comments", () => {
    expect(parseValues("# comment\n\nPERSON: John Tan\nEMAIL_ADDRESS: a@b.com\n")).toEqual([
      { type: "PERSON", value: "John Tan" },
      { type: "EMAIL_ADDRESS", value: "a@b.com" },
    ]);
  });

  it("rejects lines without a colon", () => {
    expect(() => parseValues("no colon here")).toThrow(/TYPE: value/);
  });
});

describe("allOccurrences", () => {
  it("finds every occurrence including repeats", () => {
    expect(allOccurrences("GME and GME", "GME")).toEqual([
      { start: 0, end: 3 },
      { start: 8, end: 11 },
    ]);
  });

  it("returns empty when absent", () => {
    expect(allOccurrences("hello", "GME")).toEqual([]);
  });

  it("matches case-insensitively with exact original offsets", () => {
    expect(allOccurrences("hello GME bye", "gme")).toEqual([{ start: 6, end: 9 }]);
  });

  it("collapses whitespace runs but keeps exact offsets", () => {
    expect(allOccurrences("a GAMUDA   BERHAD b", "Gamuda Berhad")).toEqual([{ start: 2, end: 17 }]);
  });
});

describe("resolveAllOccurrences", () => {
  it("finds values across pages and stores the true slice", () => {
    const pages = ["GAMUDA   BERHAD has", "and Gamuda Berhad here"];
    const { entities, misses, multiSurface } = resolveAllOccurrences(pages, [
      { type: "ORGANIZATION", value: "Gamuda Berhad" },
    ]);
    expect(misses).toEqual([]);
    expect(entities).toHaveLength(2);
    for (const e of entities) expect(pages[e.page - 1]!.slice(e.start, e.end)).toBe(e.value);
    expect(multiSurface).toHaveLength(1);
    expect(multiSurface[0]!.surfaces.sort()).toEqual(["GAMUDA   BERHAD", "Gamuda Berhad"]);
  });

  it("still reports genuine misses", () => {
    const { misses } = resolveAllOccurrences(["nothing here"], [{ type: "PERSON", value: "John Tan" }]);
    expect(misses).toEqual(["PERSON: John Tan"]);
  });
});
