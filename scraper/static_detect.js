// Decide whether a page's raw HTML (no JavaScript executed) already holds its
// real content, or whether it needs a headless browser to render.
import { JSDOM } from "jsdom";

// Containers that client-side frameworks mount into. When one of these exists
// but is (nearly) empty in the raw HTML, the content is rendered by JS.
const APP_MOUNT_SELECTORS = [
  "#root",
  "#app",
  "#__next",
  "#__nuxt",
  "#___gatsby",
  "#svelte",
  "[data-reactroot]",
  "[ng-app]",
  "[ng-version]",
  "app-root",
];

const MIN_BODY_WORDS_WITHOUT_MAIN = 600;

const JS_REQUIRED_PATTERN =
  /enable javascript|javascript (is )?(required|disabled)|requires javascript|turn on javascript/i;

const wordCount = (text) =>
  String(text || "")
    .split(/\s+/)
    .filter(Boolean).length;

const visibleText = (element) => {
  if (!element) return "";
  const clone = element.cloneNode(true);
  for (const node of clone.querySelectorAll("script, style, noscript, template, svg")) {
    node.remove();
  }
  return (clone.textContent || "").replace(/\s+/g, " ").trim();
};

/**
 * Inspect raw HTML for signs that it is a client-rendered shell.
 * Returns { shell, reasons, mainWords, bodyWords }.
 */
export const analyzeStaticHtml = (html) => {
  const reasons = [];
  const source = String(html || "");
  let document;
  try {
    document = new JSDOM(source).window.document;
  } catch {
    return { shell: true, reasons: ["unparseable"], mainWords: 0, bodyWords: 0 };
  }

  for (const selector of APP_MOUNT_SELECTORS) {
    const mount = document.querySelector(selector);
    if (mount && wordCount(visibleText(mount)) < 60) {
      reasons.push(`empty-mount:${selector}`);
      break;
    }
  }

  for (const noscript of document.querySelectorAll("noscript")) {
    if (JS_REQUIRED_PATTERN.test(noscript.textContent || "")) {
      reasons.push("noscript-requires-js");
      break;
    }
  }

  const bodyText = visibleText(document.body);
  const bodyWords = wordCount(bodyText);
  let inlineScriptBytes = 0;
  for (const script of document.querySelectorAll("script")) {
    inlineScriptBytes += (script.textContent || "").length;
  }
  if (source.length > 0 && inlineScriptBytes / source.length > 0.6 && bodyWords < 400) {
    reasons.push("script-heavy");
  }

  // Real server-rendered content almost always lives in a main/article region.
  let mainWords = 0;
  for (const region of document.querySelectorAll("main, article, [role='main']")) {
    mainWords = Math.max(mainWords, wordCount(visibleText(region)));
  }
  // Many server-rendered sites (e.g. legal/gov portals) use no semantic
  // regions at all; only treat a missing main region as a shell signal when
  // the page also has little text (nav/footer/SEO chrome only).
  if (mainWords < 80 && bodyWords < MIN_BODY_WORDS_WITHOUT_MAIN) {
    reasons.push("no-main-content");
  }

  return { shell: reasons.length > 0, reasons, mainWords, bodyWords };
};

const contentWords = (markdown) =>
  new Set(
    String(markdown || "")
      .toLowerCase()
      .match(/[\p{L}\p{N}]{4,}/gu) || [],
  );

/**
 * Fraction of the browser-rendered page's distinct words that the static
 * extraction also contains. 1 = static has everything the browser saw.
 */
export const contentCoverage = (staticMarkdown, browserMarkdown) => {
  const browserWords = contentWords(browserMarkdown);
  if (browserWords.size === 0) return 1;
  const staticWords = contentWords(staticMarkdown);
  let shared = 0;
  for (const word of browserWords) {
    if (staticWords.has(word)) shared += 1;
  }
  return shared / browserWords.size;
};

/**
 * Remembers per host whether static HTML was enough ("static") or a browser
 * render was needed ("browser"), learned by comparing both once.
 */
export const createHostModeCache = ({
  ttlMs = 3 * 24 * 60 * 60 * 1000,
  maxEntries = 5000,
  now = () => Date.now(),
} = {}) => {
  const entries = new Map();
  return {
    get(host) {
      const entry = entries.get(host);
      if (!entry) return null;
      if (entry.expiresAt <= now()) {
        entries.delete(host);
        return null;
      }
      return entry.mode;
    },
    set(host, mode) {
      entries.delete(host);
      if (entries.size >= maxEntries) {
        entries.delete(entries.keys().next().value);
      }
      entries.set(host, { mode, expiresAt: now() + ttlMs });
    },
    delete(host) {
      entries.delete(host);
    },
  };
};
