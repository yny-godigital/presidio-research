// eval/extract.ts — WHAT: extracts one text string per PDF page via PyMuPDF words.
// WHY: every label offset points into this exact text, so the extractor is pinned.
// INPUT: a PDF path. OUTPUT: page texts derived from the same word list as the boxes.
// EXAMPLE: extractPdfPages("demo/inputs/a.pdf") -> ["page one text", ...]
import { execFile } from "node:child_process";
import { promisify } from "node:util";

// Labels record this id when a human or tool creates them. run.ts refuses to
// score a label from any other extractor, because the offsets would not match
// the current page text.
export const EXTRACTOR_ID = "pymupdf-1.28.2+ocr";

export type PdfWord = { t: string; x0: number; y0: number; x1: number; y1: number; ocr: boolean };
export type PdfPage = { text: string; words: PdfWord[] };

const execFileAsync = promisify(execFile);
const WORD_KEYS = ["t", "x0", "y0", "x1", "y1", "ocr"] as const;

function parseExtractorJson(stdout: string, pdfPath: string): PdfPage[] {
    // Reject malformed bridge output before any offset can depend on it.
    let raw: unknown;
    try { raw = JSON.parse(stdout); }
    catch { throw new Error(`extract_pdf.py returned invalid JSON for ${pdfPath}`); }
    if (typeof raw !== "object" || raw === null || !Array.isArray((raw as { pages: unknown }).pages))
        throw new Error(`extract_pdf.py returned bad shape for ${pdfPath}: want {pages: [{text, words}]}`);
    return (raw as { pages: PdfPage[] }).pages.map((p, i) => {
        if (typeof p?.text !== "string" || !Array.isArray(p?.words))
            throw new Error(`extract_pdf.py page ${i + 1} of ${pdfPath} is malformed`);
        for (const w of p.words) {
            if (typeof w?.t !== "string" || typeof w?.ocr !== "boolean"
                || WORD_KEYS.some((k) => k !== "t" && k !== "ocr" && typeof (w as Record<string, unknown>)[k] !== "number"))
                throw new Error(`extract_pdf.py word on page ${i + 1} of ${pdfPath} is malformed`);
        }
        return p;
    });
}

export async function extractPdfWords(pdfPath: string): Promise<PdfPage[]> {
    // Python owns extraction; stdout carries only JSON, diagnostics ride stderr.
    let out: { stdout: string; stderr: string };
    try {
        out = await execFileAsync("python3", ["eval/extract_pdf.py", pdfPath], { maxBuffer: 64 * 1024 * 1024 });
    } catch (err) {
        const e = err as { stderr?: string; message?: string };
        const detail = (e.stderr ?? e.message ?? String(err)).trim();
        throw new Error(`extract_pdf.py failed for ${pdfPath}: ${detail}`);
    }
    return parseExtractorJson(out.stdout, pdfPath);
}

export async function extractPdfPages(pdfPath: string): Promise<string[]> {
    // Page text derives from the same word list as the boxes, so detection
    // offsets and future burn-in boxes cannot drift apart.
    return (await extractPdfWords(pdfPath)).map((p) => p.text);
}
