// eval/schema.ts — WHAT: defines the label JSON shape and validates it.
// WHY: bad pages or stale offsets would corrupt every score silently.
// INPUT: extracted pages plus a LabelFile. OUTPUT: void, or a thrown Label error.
// EXAMPLE: validateLabel(pages, label) throws on slice mismatch.
export type LabeledEntity = {
  type: string;
  value: string;
  page: number;
  start: number;
  end: number;
};

export type LabelFile = {
  source: string;
  extractor: string;
  entities: LabeledEntity[];
};

export function validateLabel(pages: string[], label: LabelFile): void {
  for (const e of label.entities) {
    if (!Number.isInteger(e.page) || e.page < 1 || e.page > pages.length) {
      throw new Error(`Label error: page ${e.page} out of range 1..${pages.length}`);
    }
    const text = pages[e.page - 1] ?? "";
    if (e.start < 0 || e.end > text.length || e.start >= e.end) {
      throw new Error(`Label error: bad offsets ${e.start}..${e.end} on page ${e.page}`);
    }
    const slice = text.slice(e.start, e.end);
    if (slice !== e.value) {
      throw new Error(
        `Label error: slice mismatch on page ${e.page} ${e.start}..${e.end}: expected ${JSON.stringify(e.value)} got ${JSON.stringify(slice)}`
      );
    }
  }
}
