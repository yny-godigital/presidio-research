// eval/spans.ts — WHAT: one overlap rule for all eval scoring and labeling.
// WHY: prelabel, resolve-labels, and score each used a slightly different rule.
// INPUT: spans with page, start, end, and optional score. OUTPUT: kept and dropped lists.
// EXAMPLE: keepLongestNonOverlapping(entities) -> { kept, dropped }

export function spansOverlap(a: { start: number; end: number }, b: { start: number; end: number }): boolean {
  // Adjacent spans share an edge but no characters, so they do not overlap.
  return a.start < b.end && b.start < a.end;
}

export function keepLongestNonOverlapping<
  T extends { page: number; start: number; end: number; score?: number },
>(entities: T[]): { kept: T[]; dropped: T[] } {
  // The sort order is the whole rule: page, then start, then the longer span
  // first, then the higher score. Example on one page: A 0..10 and B 5..8. A
  // sorts first, so B overlaps it and is dropped.
  const sorted = [...entities].sort(
    (x, y) =>
      x.page - y.page ||
      x.start - y.start ||
      y.end - y.start - (x.end - x.start) ||
      (y.score ?? 0) - (x.score ?? 0)
  );
  const kept: T[] = [];
  const dropped: T[] = [];
  let lastPage = -1;
  let lastEnd = -1;
  for (const e of sorted) {
    // A new page resets the overlap window; on the same page only spans at or after lastEnd survive.
    if (e.page !== lastPage || e.start >= lastEnd) {
      kept.push(e);
      lastPage = e.page;
      lastEnd = e.end;
    } else {
      dropped.push(e);
    }
  }
  return { kept, dropped };
}
