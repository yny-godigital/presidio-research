// tests/eval-extract.test.ts
import { describe, expect, it } from "vitest";
import { readFileSync, writeFileSync } from "node:fs";
import { PDFDocument, StandardFonts } from "pdf-lib";
import { EXTRACTOR_ID, extractPdfPages, extractPdfWords } from "../eval/extract.js";
import { parseValues, resolveAllOccurrences } from "../eval/resolve-labels.js";

async function makeOnePagePdf(text: string): Promise<string> {
  const doc = await PDFDocument.create();
  const page = doc.addPage([400, 200]);
  page.drawText(text, { x: 20, y: 100, size: 12, font: await doc.embedFont(StandardFonts.Helvetica) });
  const path = "/tmp/opencode/eval-fixture.pdf";
  writeFileSync(path, await doc.save());
  return path;
}

describe("extractPdfPages", () => {
  it("returns one page whose text contains the drawn string", async () => {
    const path = await makeOnePagePdf("Hello John Tan PRJ-2026-001");
    const pages = await extractPdfPages(path);
    expect(pages).toHaveLength(1);
    expect(pages[0]).toContain("John Tan");
  });

  // Case 1 — WHY: kern-split garble ("Fu rther", "L OI") must never return.
  it("reads the LOI fixture clean without kern-split garble", async () => {
    const pages = await extractPdfPages("demo/inputs/further_to_issuance_of_the_loi.pdf");
    expect(pages.length).toBeGreaterThan(0);
    expect(pages[0]).toContain("Further to issuance of the LOI");
    for (const garble of ["Fu rther", "L OI", "reach ed", "T o enter"])
      expect(pages[0]).not.toContain(garble);
  });

  // Case 2 — WHY: every PII span must sit inside word boxes on the same page.
  // Fresh spans are resolved from the .values.txt against the NEW text (the
  // legacy JSON offsets point into pdfjs text and cannot be reused here).
  it("covers every resolved PII span with word boxes on the same page", async () => {
    const pages = await extractPdfWords("demo/inputs/further_to_issuance_of_the_loi.pdf");
    const wanted = parseValues(readFileSync(
      "demo/inputs/labels/further_to_issuance_of_the_loi.values.txt", "utf8"));
    const { entities, misses } = resolveAllOccurrences(pages.map((p) => p.text), wanted);
    expect(misses).toEqual([]);
    expect(entities.length).toBeGreaterThan(0);
    for (const e of entities) {
      const page = pages[e.page - 1];
      if (page === undefined) throw new Error("page out of range: " + e.page);
      // Rebuild coverage from the word list: each word box covers its own
      // characters plus the separator that follows it, so a span running
      // across words still lands inside the boxes. Coverage is per character
      // because a word token can be wider than the span it holds ("(PCI)").
      const ats: number[] = [];
      let cursor = 0;
      for (const w of page.words) {
        const at = page.text.indexOf(w.t, cursor);
        expect(at).toBeGreaterThanOrEqual(0);
        ats.push(at);
        cursor = at + w.t.length;
      }
      const covered: boolean[] = new Array(page.text.length).fill(false);
      for (let n = 0; n < ats.length; n++) {
        const stop = n + 1 < ats.length ? ats[n + 1] : page.text.length;
        for (let i = ats[n]; i < stop; i++) covered[i] = true;
      }
      for (let i = e.start; i < e.end; i++) expect(covered[i]).toBe(true);
    }
  });

  // Case 3 — WHY: pure scans (zero text chars) must OCR instead of yielding "".
  it("OCRs a zero-char scanned fixture with ocr-flagged words", async () => {
    const doc = await PDFDocument.create();
    const png = await doc.embedPng(readFileSync("/tmp/scan_words.png"));
    // Page is 1200x400 px at 300 DPI so the OCR render stays pixel-identical.
    const page = doc.addPage([288, 96]);
    page.drawImage(png, { x: 0, y: 0, width: 288, height: 96 });
    const scanPath = "/tmp/eval-scan-fixture.pdf";
    writeFileSync(scanPath, await doc.save());
    const pages = await extractPdfWords(scanPath);
    expect(pages).toHaveLength(1);
    expect(pages[0].text).toContain("Scan check 123");
    expect(pages[0].words.length).toBeGreaterThan(0);
    for (const w of pages[0].words) expect(w.ocr).toBe(true);
  }, 60000);

  // Case 4 — WHY: stale pdfjs labels must fail the extractor guard, never score.
  it("rejects a legacy pdfjs-dist-4.2.67 label at the extractor check", () => {
    expect(EXTRACTOR_ID).toBe("pymupdf-1.28.2+ocr");
    const legacy = { extractor: "pdfjs-dist-4.2.67" };
    expect(legacy.extractor).not.toBe(EXTRACTOR_ID);
  });
});
