import Ajv2020 from "ajv/dist/2020.js";
import { readFileSync } from "node:fs";

export interface BrowserObservation {
  observation_id: string;
  url: string;
  text: string;
  trust: "UNTRUSTED_PAGE_CONTENT";
  scroll_y: number;
  elements: { element_ref: string; tag: string; label: string }[];
}

export interface BrowserActionResult {
  status: "VERIFIED" | "UNVERIFIED" | "FAILED";
  trace_id: string;
  session_id: string | null;
  retryable: false;
  outcome_unknown: boolean;
  evidence_ref: string;
  before_evidence_ref?: string;
  error_code?: string;
  observation?: BrowserObservation;
  browser?: { browser_version: string; process_id: number };
}

const schema = JSON.parse(readFileSync(new URL("../schemas/browser-tool-v1.schema.json", import.meta.url), "utf8"));
const validate = new Ajv2020({ strict: true, allErrors: true }).compile(schema);
export function isBrowserActionResult(value: unknown): value is BrowserActionResult {
  return validate(value) === true;
}
