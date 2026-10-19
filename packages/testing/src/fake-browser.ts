import { isBrowserActionResult, type BrowserActionResult } from "../../protocols/src/browser.js";

/** Scripted Tool outputs for tests. Does not launch or simulate a real browser. */
export class FakeBrowser {
  private readonly results: BrowserActionResult[];

  constructor(results: unknown[]) {
    if (!results.every(isBrowserActionResult)) throw new Error("Invalid browser result");
    this.results = structuredClone(results);
  }

  takeResult(): BrowserActionResult {
    const result = this.results.shift();
    if (!result) throw new Error("No scripted browser result");
    return structuredClone(result);
  }
}
