// eval/roundtrip.ts — WHAT: scores live anonymize-then-restore roundtrip per page.
// WHY: local simulation passes while the service diverges; score the real path.
// INPUT: page texts plus a client with anonymize/restore. OUTPUT: scores plus per-page detail.
// EXAMPLE: scoreRoundtrip(pages, client) -> { totalTokens: 4, restoredTokens: 4, restoreRate: 1, pages: [...] }
import {
  ServiceHttpError,
  type AnalyzerClient,
  type AnonymizeMappingEntry,
} from "./analyzer-client.js";

export type RoundtripPage = {
  page: number;
  anonymized: string;
  restored: string;
  mapping: AnonymizeMappingEntry[];
  blocked: string[];
  skipped: boolean;
};

export type RoundtripScore = {
  totalTokens: number;
  restoredTokens: number;
  restoreRate: number;
  pages: RoundtripPage[];
};

function blockedNames(err: ServiceHttpError): string[] {
  try {
    const payload = JSON.parse(err.body) as { detail?: string };
    const match = payload.detail?.match(/Blocked by policy:\s*(.+)/);
    if (match?.[1]) return match[1].split(",").map((s) => s.trim()).filter(Boolean);
  } catch {
    /* fall through to generic marker */
  }
  return ["unknown"];
}

export async function scoreRoundtrip(
  texts: string[],
  client: Pick<AnalyzerClient, "anonymize" | "restore">,
): Promise<RoundtripScore> {
  // A page's tokens count as restored only when the whole page returns
  // byte-identical, so one bad substitution fails every token on that page.
  // Empty pages are skipped and add zero tokens; a 422 policy block records
  // the blocked entity types and adds zero tokens.
  let totalTokens = 0;
  let restoredTokens = 0;
  const pages: RoundtripPage[] = [];
  for (let i = 0; i < texts.length; i++) {
    const original = texts[i] ?? "";
    if (!original.trim()) {
      pages.push({ page: i + 1, anonymized: "", restored: "", mapping: [], blocked: [], skipped: true });
      continue;
    }
    try {
      const anon = await client.anonymize(original);
      const back = await client.restore(anon.text, anon.mapping);
      totalTokens += anon.mapping.length;
      if (back === original) restoredTokens += anon.mapping.length;
      pages.push({ page: i + 1, anonymized: anon.text, restored: back, mapping: anon.mapping, blocked: [], skipped: false });
    } catch (err) {
      if (err instanceof ServiceHttpError && err.status === 422) {
        pages.push({ page: i + 1, anonymized: "", restored: "", mapping: [], blocked: blockedNames(err), skipped: false });
        continue;
      }
      throw err;
    }
  }
  return { totalTokens, restoredTokens, restoreRate: totalTokens === 0 ? 1 : restoredTokens / totalTokens, pages };
}
