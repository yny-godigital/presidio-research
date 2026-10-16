// tests/eval-roundtrip.test.ts
import { describe, expect, it } from "vitest";
import { scoreRoundtrip } from "../eval/roundtrip.js";
import { ServiceHttpError } from "../eval/analyzer-client.js";
import type { AnonymizeMappingEntry } from "../eval/analyzer-client.js";

function stubClient(entries: AnonymizeMappingEntry[], restored: string, anonymized = "<PERSON_001>") {
  return {
    anonymize: async () => ({ text: anonymized, mapping: entries }),
    restore: async () => restored,
  };
}

describe("scoreRoundtrip", () => {
  it("scores 1.0 when restore returns the byte-identical original", async () => {
    const mapping = [{ token: "<PERSON_001>", entity_type: "PERSON", original: "John Tan" }];
    const s = await scoreRoundtrip(["hello John Tan"], stubClient(mapping, "hello John Tan"));
    expect(s.totalTokens).toBe(1);
    expect(s.restoredTokens).toBe(1);
    expect(s.restoreRate).toBe(1);
  });

  it("scores 0 for entries on a page whose restore differs", async () => {
    const mapping = [{ token: "<PERSON_001>", entity_type: "PERSON", original: "John Tan" }];
    const s = await scoreRoundtrip(["hello John Tan"], stubClient(mapping, "hello Jane Doe"));
    expect(s.totalTokens).toBe(1);
    expect(s.restoredTokens).toBe(0);
    expect(s.restoreRate).toBe(0);
  });

  it("scores restoreRate 1 when the service finds no PII", async () => {
    const s = await scoreRoundtrip(["plain text"], stubClient([], "plain text", "plain text"));
    expect(s.totalTokens).toBe(0);
    expect(s.restoreRate).toBe(1);
  });

  it("records a blocked page without throwing on a block-policy 422", async () => {
    const blocked = {
      anonymize: async () => {
        throw new ServiceHttpError(422, JSON.stringify({ detail: "Blocked by policy: API_KEY" }));
      },
      restore: async () => "",
    };
    const s = await scoreRoundtrip(["key sk-abc"], blocked);
    expect(s.totalTokens).toBe(0);
    expect(s.restoredTokens).toBe(0);
    expect(s.restoreRate).toBe(1);
    expect(s.pages[0]?.blocked).toEqual(["API_KEY"]);
  });

  it("skips empty pages without calling the service", async () => {
    let calls = 0;
    const counting = {
      anonymize: async () => { calls++; return { text: "", mapping: [] }; },
      restore: async () => "",
    };
    const s = await scoreRoundtrip(["   "], counting);
    expect(calls).toBe(0);
    expect(s.totalTokens).toBe(0);
  });
});
