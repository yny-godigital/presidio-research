import { describe, expect, it } from "vitest";
import { parseOnlySet } from "../eval/cli.js";
import { spansOverlap, keepLongestNonOverlapping } from "../eval/spans.js";

describe("parseOnlySet", () => {
  it("returns an empty set for an empty string", () => {
    expect(parseOnlySet("")).toEqual(new Set());
  });

  it("splits on commas and trims whitespace", () => {
    expect(parseOnlySet(" a ,b,, ")).toEqual(new Set(["a", "b"]));
  });
});

describe("spansOverlap", () => {
  it("treats adjacent spans as non-overlapping", () => {
    expect(spansOverlap({ start: 0, end: 5 }, { start: 5, end: 9 })).toBe(false);
  });

  it("treats identical spans as overlapping", () => {
    expect(spansOverlap({ start: 2, end: 6 }, { start: 2, end: 6 })).toBe(true);
  });
});

describe("keepLongestNonOverlapping", () => {
  it("keeps the longest span when two overlap on the same page", () => {
    const input = [
      { page: 1, start: 0, end: 5, type: "PERSON" },
      { page: 1, start: 0, end: 10, type: "PERSON" },
    ];
    const { kept, dropped } = keepLongestNonOverlapping(input);
    expect(kept).toEqual([{ page: 1, start: 0, end: 10, type: "PERSON" }]);
    expect(dropped.length).toBe(1);
  });

  it("keeps same offsets on different pages", () => {
    const input = [
      { page: 1, start: 0, end: 5, type: "PERSON" },
      { page: 2, start: 0, end: 5, type: "PERSON" },
    ];
    const { kept } = keepLongestNonOverlapping(input);
    expect(kept.length).toBe(2);
  });
});
