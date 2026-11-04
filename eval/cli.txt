// eval/cli.ts — WHAT: parses CLI flags for the eval tools.
// WHY: run, prelabel, and resolve-labels each parsed argv the same way.
// INPUT: process.argv. OUTPUT: flag values and the --only stem set.
// EXAMPLE: npx tsx eval/run.ts --only pilot_a,pilot_b

export function parseArg(name: string, fallback: string): string {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

export function parseOnlySet(raw: string): Set<string> {
  return new Set(
    raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
  );
}
