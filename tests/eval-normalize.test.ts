// tests/eval-normalize.test.ts
import { describe, expect, it } from "vitest";
import { normalizeStem } from "../eval/normalize.js";

describe("normalizeStem", () => {
  it("lowercases and collapses non-alphanumerics to single underscores", () => {
    expect(normalizeStem("A-3.0 - Sub-Contract Agreement.pdf")).toBe(
      "a_3_0_sub_contract_agreement"
    );
  });

  it("trims edge underscores and keeps plain names stable", () => {
    expect(normalizeStem("  LET-002 (2).pdf")).toBe("let_002_2");
    expect(normalizeStem("plain.pdf")).toBe("plain");
  });
});
