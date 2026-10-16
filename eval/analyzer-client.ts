// eval/analyzer-client.ts — WHAT: HTTP client for the Presidio service
//   (/analyze, /anonymize, /restore).
// WHY: the eval harness scores the live service, not a local simulation, so
//   every call goes over HTTP with a 10 second timeout.
// INPUT: service base URL. OUTPUT: detections, anonymized text plus mapping,
//   restored text.
// EXAMPLE: new PresidioAnalyzerClient("http://127.0.0.1:3000")
export type DetectedEntity = {
  entityType: string;
  start: number;
  end: number;
  score: number;
};

export interface AnalyzerClient {
  analyze(text: string): Promise<DetectedEntity[]>;
  anonymize(text: string): Promise<AnonymizeResult>;
  restore(text: string, mapping: AnonymizeMappingEntry[]): Promise<string>;
}

export type AnonymizeMappingEntry = {
  token: string;
  entity_type: string;
  original: string;
};

export type AnonymizeResult = {
  text: string;
  mapping: AnonymizeMappingEntry[];
};

export class ServiceHttpError extends Error {
  readonly status: number;
  readonly body: string;
  constructor(status: number, body: string) {
    super(`Presidio service returned HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

type PresidioResult = {
  entity_type: string;
  start: number;
  end: number;
  score: number;
};

export class PresidioAnalyzerClient implements AnalyzerClient {
  private readonly baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async analyze(text: string): Promise<DetectedEntity[]> {
    const response = await fetch(`${this.baseUrl}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        language: "en",
      }),
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) {
      throw new Error(`Presidio analyzer returned HTTP ${response.status}`);
    }
    const results = (await response.json()) as PresidioResult[];
    return results.map((r) => ({
      entityType: r.entity_type,
      start: r.start,
      end: r.end,
      score: r.score,
    }));
  }

  async anonymize(text: string): Promise<AnonymizeResult> {
    const response = await fetch(`${this.baseUrl}/anonymize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, language: "en", mode: "reversible" }),
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) {
      throw new ServiceHttpError(response.status, await response.text());
    }
    const payload = (await response.json()) as { text: string; mapping?: AnonymizeMappingEntry[] };
    return { text: payload.text, mapping: payload.mapping ?? [] };
  }

  async restore(text: string, mapping: AnonymizeMappingEntry[]): Promise<string> {
    const response = await fetch(`${this.baseUrl}/restore`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, mapping }),
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) {
      throw new ServiceHttpError(response.status, await response.text());
    }
    const payload = (await response.json()) as { text: string };
    return payload.text;
  }
}
