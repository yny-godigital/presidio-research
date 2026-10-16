// eval/normalize.ts — WHAT: maps file names to comparable stems.
// WHY: PDFs and labels pair by stem, so My Doc.PDF matches my_doc.json.
// INPUT: a file name. OUTPUT: lowercase stem with single underscores.
// EXAMPLE: normalizeStem("A-3.0 - Sub.pdf") -> "a_3_0_sub"
export function normalizeStem(basename: string): string {
  const withoutExt = basename.replace(/\.pdf$/i, "");
  return withoutExt
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}
