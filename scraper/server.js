import express from "express";
import puppeteer from "puppeteer";
import { Readability } from "@mozilla/readability";
import { JSDOM } from "jsdom";
import TurndownService from "turndown";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import {
  CHROME_LAUNCH_ARGS,
  documentToMarkdown,
  fetchDocument,
  isDirectTextDocument,
} from "./http_fetch.js";
import { validateUrl } from "./url_safety.js";
import {
  analyzeStaticHtml,
  contentCoverage,
  createHostModeCache,
} from "./static_detect.js";
import { exportMarpPresentation } from "./marp_export.js";

const app = express();
app.use(express.json({ limit: process.env.SCRAPER_JSON_LIMIT || "15mb" }));

const port = process.env.SCRAPER_PORT || 3001;
const textLimit = Number(process.env.SCRAPE_TEXT_LIMIT || 20000);

let browser;
let blockedSelectorsConfig;

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const loadBlockedSelectorsConfig = () => {
  if (blockedSelectorsConfig) {
    return blockedSelectorsConfig;
  }
  const configPath = path.join(__dirname, "blocked_selectors.json");
  try {
    const raw = fs.readFileSync(configPath, "utf-8");
    const parsed = JSON.parse(raw);
    blockedSelectorsConfig = {
      blockedWords: new Set(
        Array.isArray(parsed.blocked_words)
          ? parsed.blocked_words
              .filter((value) => typeof value === "string")
              .map((value) => value.toLowerCase())
          : [],
      ),
      blockedSpecificSelectors: Array.isArray(parsed.blocked_specific_selectors)
        ? parsed.blocked_specific_selectors.filter(
            (value) => typeof value === "string" && value.trim(),
          )
        : [],
      blockedTags: Array.isArray(parsed.blocked_tags)
        ? parsed.blocked_tags
            .filter((value) => typeof value === "string" && value.trim())
            .map((value) => value.toLowerCase())
        : [],
      blockedLinkWords: new Set(
        Array.isArray(parsed.blocked_link_words)
          ? parsed.blocked_link_words
              .filter((value) => typeof value === "string")
              .map((value) => value.toLowerCase())
          : [],
      ),
    };
  } catch (error) {
    console.warn("Failed to load blocked_selectors.json, using empty config", error);
    blockedSelectorsConfig = {
      blockedWords: new Set(),
      blockedSpecificSelectors: [],
      blockedTags: [],
      blockedLinkWords: new Set(),
    };
  }
  return blockedSelectorsConfig;
};

const tokenizeValue = (value) => {
  if (!value) return [];
  return value
    .toLowerCase()
    .split(/[^a-z0-9]+/g)
    .map((item) => item.trim())
    .filter(Boolean);
};

const splitCamelCase = (value) => {
  if (!value) return "";
  return String(value).replace(/([a-z0-9])([A-Z])/g, "$1 $2");
};

const elementIdentityTokens = (element) => {
  const classValue = Array.from(element.classList || []).join(" ");
  const idValue = element.id || "";
  return tokenizeValue(`${splitCamelCase(classValue)} ${splitCamelCase(idValue)}`);
};

const hasBlockedWord = (tokens, blockedWords) => {
  for (const token of tokens) {
    if (blockedWords.has(token)) {
      return true;
    }
  }
  return false;
};

const removeElement = (element) => {
  if (!element) return;
  if (element.tagName === "HTML" || element.tagName === "BODY") return;
  element.remove();
};

const normalizeText = (value) => String(value || "").replace(/\s+/g, " ").trim();

const textStats = (element) => {
  const text = normalizeText(element.textContent || "");
  const textLength = text.length;
  let linkTextLength = 0;
  const links = Array.from(element.querySelectorAll("a"));
  for (const link of links) {
    linkTextLength += normalizeText(link.textContent || "").length;
  }
  const linkCount = links.length;
  const linkDensity = textLength > 0 ? linkTextLength / textLength : 0;
  return { textLength, linkTextLength, linkCount, linkDensity };
};

const hasLikelyContentStructure = (element) => {
  if (element.querySelector("article, main, p, h1, h2, h3, h4, table, pre, code, blockquote")) {
    return true;
  }
  const paragraphs = element.querySelectorAll("p").length;
  const headings = element.querySelectorAll("h1, h2, h3, h4").length;
  return paragraphs >= 2 || (paragraphs >= 1 && headings >= 1);
};

const shouldPruneByStructure = (element) => {
  const tagName = element.tagName;
  if (tagName === "ARTICLE" || tagName === "MAIN") return false;
  if (element.closest("article")) return false;
  if (hasLikelyContentStructure(element)) return false;

  const identityTokens = elementIdentityTokens(element);
  const looksNavLike = ["nav", "menu", "sidebar", "footer", "header", "breadcrumb", "related"].some((token) =>
    identityTokens.includes(token),
  );
  const formControls = element.querySelectorAll("input, button, select, textarea, form").length;
  const { textLength, linkCount, linkDensity } = textStats(element);

  if (looksNavLike && linkCount >= 3) return true;
  if (linkCount >= 8 && textLength <= 400 && linkDensity >= 0.55) return true;
  if (formControls >= 3 && textLength <= 260) return true;
  if (linkCount >= 4 && textLength <= 120 && linkDensity >= 0.75) return true;
  return false;
};

const compactMarkdown = (markdown) => {
  const normalized = String(markdown || "")
    .replace(/\r\n/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return normalized;
};

const markdownWordCount = (markdown) =>
  String(markdown || "")
    .split(/\s+/g)
    .filter((word) => /[\p{L}\p{N}]/u.test(word)).length;

const looksBoilerplateMarkdown = (markdown) => {
  const text = String(markdown || "").toLowerCase();
  if (!text) return true;
  const boilerplateHits = [
    "cookie",
    "consent",
    "privacy",
    "terms",
    "policy",
    "gdpr",
    "sīkdat",
    "privāt",
    "noteikumi",
  ].filter((token) => text.includes(token)).length;
  // A short, legal/consent-heavy extract usually means Readability selected the wrong block.
  return boilerplateHits >= 2 && markdownWordCount(text) < 220;
};

const cleanupDocument = (document) => {
  const config = loadBlockedSelectorsConfig();

  for (const tagName of config.blockedTags) {
    for (const element of document.querySelectorAll(tagName)) {
      removeElement(element);
    }
  }

  for (const selector of config.blockedSpecificSelectors) {
    try {
      for (const element of document.querySelectorAll(selector)) {
        removeElement(element);
      }
    } catch {
      // Ignore invalid selectors in config.
    }
  }

  const allElements = Array.from(document.querySelectorAll("*"));
  for (const element of allElements) {
    if (!element.isConnected) continue;
    if (element.tagName === "HTML" || element.tagName === "BODY") continue;

    const identityTokens = elementIdentityTokens(element);
    const blockedByIdentity = hasBlockedWord(identityTokens, config.blockedWords);
    if (
      (blockedByIdentity && !hasLikelyContentStructure(element)) ||
      shouldPruneByStructure(element)
    ) {
      removeElement(element);
      continue;
    }

    if (element.tagName === "A") {
      const href = (element.getAttribute("href") || "").toLowerCase();
      const combined = `${href} ${element.id || ""} ${Array.from(element.classList || []).join(" ")}`;
      const linkTokens = tokenizeValue(combined);
      if (
        hasBlockedWord(linkTokens, config.blockedLinkWords)
      ) {
        removeElement(element);
      }
    }
  }
};

const getBrowser = async () => {
  if (browser?.connected) {
    return browser;
  }
  if (browser) {
    try {
      await browser.close();
    } catch {
      // ignore close errors on a dead browser
    }
    browser = null;
  }

  const launchOptions = {
    headless: true,
    args: CHROME_LAUNCH_ARGS,
    ignoreHTTPSErrors: true,
  };
  // Optional override for custom images; default is Puppeteer's image-bundled Chrome.
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    launchOptions.executablePath = process.env.PUPPETEER_EXECUTABLE_PATH;
  }

  try {
    browser = await puppeteer.launch(launchOptions);
  } catch (error) {
    const message =
      error && typeof error === "object" && "message" in error
        ? String(error.message)
        : String(error);
    throw new Error(
      `Failed to launch Puppeteer Chrome (is it installed in the image via ` +
        `"puppeteer browsers install chrome"?): ${message}`,
    );
  }
  browser.on("disconnected", () => {
    browser = null;
  });
  return browser;
};

const DEFAULT_VIEWPORT = { width: 1366, height: 1800 };

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const preparePage = async (page, browserInstance) => {
  await page.setViewport(DEFAULT_VIEWPORT);
  // Match the launched Chrome version/TLS. Spoofing an old fixed Chrome UA
  // (e.g. Chrome/120) trips SiteGround and similar WAFs with 403.
  const userAgent = String(await browserInstance.userAgent() || "").replace(
    /HeadlessChrome/g,
    "Chrome",
  );
  await page.setUserAgent(userAgent);
  await page.setExtraHTTPHeaders({
    "Accept-Language": "en-US,en;q=0.9",
  });
  await page.evaluateOnNewDocument(() => {
    Object.defineProperty(navigator, "webdriver", {
      get: () => undefined,
    });
  });
};

// Per-origin validation cache: pages issue hundreds of subresource requests and
// a DNS lookup per request would be slow. Short TTL limits rebinding windows;
// network isolation (scraper has no route to internal services) is the backstop.
const REQUEST_VALIDATION_TTL_MS = 30_000;
const requestValidationCache = new Map();

const validateRequestUrlCached = async (requestUrl) => {
  let key;
  try {
    const parsed = new URL(requestUrl);
    key = `${parsed.protocol}//${parsed.host}`;
  } catch {
    return false;
  }
  const now = Date.now();
  const cached = requestValidationCache.get(key);
  if (cached && cached.expires > now) {
    return cached.valid;
  }
  const valid = await validateUrl(requestUrl);
  if (requestValidationCache.size > 5000) {
    requestValidationCache.clear();
  }
  requestValidationCache.set(key, { valid, expires: now + REQUEST_VALIDATION_TTL_MS });
  return valid;
};

const attachPrivateRedirectGuard = async (page) => {
  // Async request handlers race easily in Puppeteer; resolve carefully and
  // never call continue/abort twice.
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const finish = async () => {
      try {
        if (request.isInterceptResolutionHandled?.()) {
          return;
        }
        // Validate EVERY network request (subresources, page JS fetch/XHR,
        // iframes, redirects) — not just redirected navigations — so a scraped
        // page cannot reach internal services (docker daemon, redis, backend).
        const requestUrl = request.url();
        if (!requestUrl.startsWith("data:") && !requestUrl.startsWith("blob:")) {
          const valid = await validateRequestUrlCached(requestUrl);
          if (request.isInterceptResolutionHandled?.()) {
            return;
          }
          if (!valid) {
            await request.abort();
            return;
          }
        }
        await request.continue();
      } catch {
        try {
          if (!request.isInterceptResolutionHandled?.()) {
            await request.abort();
          }
        } catch {
          // ignore
        }
      }
    };
    void finish();
  });
};

const settlePage = async (page) => {
  // Let SPA routes, deferred scripts, and hydration settle.
  try {
    await page.waitForFunction(() => document.readyState === "complete", {
      timeout: 5000,
    });
  } catch {}

  try {
    // Ad/analytics-heavy pages never go fully idle; don't wait long for them.
    await page.waitForNetworkIdle({ idleTime: 500, timeout: 2500 });
  } catch {}

  // Trigger lazy loaders (IntersectionObserver/image/content reveal).
  try {
    await page.evaluate(async () => {
      const totalHeight = Math.max(
        document.body?.scrollHeight || 0,
        document.documentElement?.scrollHeight || 0,
      );
      const viewport = window.innerHeight || 900;
      // Cap lazy-load scrolling: a few screens is enough to trigger loaders,
      // and walking very long pages costs seconds for no extra text.
      const maxHeight = Math.min(totalHeight, viewport * 6);
      let y = 0;
      while (y < maxHeight) {
        window.scrollTo(0, y);
        y += Math.max(Math.floor(viewport * 0.9), 400);
        // Small delay between scroll steps to allow lazy content to render.
        await new Promise((resolve) => setTimeout(resolve, 80));
      }
      window.scrollTo(0, 0);
    });
  } catch {}

  // Wait until the page has some visible text (soft-blocks / blank shells fail this).
  try {
    await page.waitForFunction(
      () => (document.body?.innerText || "").replace(/\s+/g, " ").trim().length > 40,
      { timeout: 3000 },
    );
  } catch {}

  // One final short settle after scrolling.
  await sleep(150);
};

// Basic quality bar for text extracted from raw HTML (no JS executed).
const STATIC_REJECT_PATTERN =
  /enable javascript|javascript (is )?(required|disabled)|just a moment|checking your browser|verify you are (a )?human|captcha|access denied/i;
const MIN_STATIC_WORDS = 120;

const isUsableStaticMarkdown = (result) => {
  const markdown = result?.markdown;
  if (!markdown) return false;
  if (markdownWordCount(markdown) < MIN_STATIC_WORDS) return false;
  if (looksBoilerplateMarkdown(markdown)) return false;
  const head = `${result.title || ""}\n${markdown.slice(0, 1500)}`;
  return !STATIC_REJECT_PATTERN.test(head);
};

// Answer mode: the LLM only needs visual context next to the markdown, so a
// viewport JPEG (~100-300KB) instead of a multi-MB full-page PNG.
const captureAnswerScreenshot = async (page) => {
  await page.setViewport(DEFAULT_VIEWPORT);
  return await page.screenshot({
    type: "jpeg",
    quality: 70,
    fullPage: false,
    encoding: "base64",
  });
};

const captureScreenshotSafe = async (page) => {
  await page.setViewport(DEFAULT_VIEWPORT);
  try {
    return await page.screenshot({
      type: "png",
      fullPage: true,
      encoding: "base64",
    });
  } catch (error) {
    const message =
      error && typeof error === "object" && "message" in error
        ? String(error.message)
        : String(error);
    if (!message.toLowerCase().includes("0 width")) {
      throw error;
    }
    // Some anti-bot/challenge pages report zero page width for fullPage screenshots.
    // Fallback to a viewport screenshot to keep scrape usable.
    return await page.screenshot({
      type: "png",
      fullPage: false,
      encoding: "base64",
    });
  }
};

const htmlFragmentToMarkdown = (html, url) => {
  const fragmentDom = new JSDOM(html || "", { url });
  cleanupDocument(fragmentDom.window.document);
  const turndown = new TurndownService({ headingStyle: "atx" });
  return compactMarkdown(
    turndown.turndown(fragmentDom.window.document.body.innerHTML || ""),
  );
};

const shouldUseBodyFallback = (articleMarkdown, bodyMarkdown) => {
  if (!bodyMarkdown) return false;
  if (!articleMarkdown) return true;

  const articleWords = markdownWordCount(articleMarkdown);
  const bodyWords = markdownWordCount(bodyMarkdown);

  if (articleWords < 50 && bodyWords > articleWords * 2.2) return true;
  if (
    articleMarkdown.length < 450 &&
    bodyMarkdown.length > articleMarkdown.length * 2.2
  ) {
    return true;
  }
  if (
    looksBoilerplateMarkdown(articleMarkdown) &&
    bodyWords > Math.max(articleWords * 1.2, 80)
  ) {
    return true;
  }
  return false;
};

const preferredContentMarkdown = (document, url) => {
  const selectors = [
    "#dp",
    "#ppd",
    "[data-testid='product-detail']",
    "[data-testid='product-details']",
    "[itemtype*='Product']",
    "main",
    "[role='main']",
    "article",
  ];
  for (const selector of selectors) {
    const element = document.querySelector(selector);
    if (!element) continue;
    const markdown = htmlFragmentToMarkdown(element.outerHTML, url);
    if (
      markdownWordCount(markdown) >= 40 &&
      !looksBoilerplateMarkdown(markdown)
    ) {
      return markdown;
    }
  }
  return "";
};

const shouldUsePreferredContent = (articleMarkdown, preferredMarkdown) => {
  if (!preferredMarkdown) return false;
  if (!articleMarkdown || looksBoilerplateMarkdown(articleMarkdown)) return true;

  const articleWords = markdownWordCount(articleMarkdown);
  const preferredWords = markdownWordCount(preferredMarkdown);
  return preferredWords >= 80 && articleWords < preferredWords * 0.4;
};

const toMarkdown = (html, url) => {
  const dom = new JSDOM(html, { url });
  const document = dom.window.document;
  const preferredMarkdown = preferredContentMarkdown(document, url);
  const reader = new Readability(document.cloneNode(true));
  const article = reader.parse();
  const bodyHtml = document.body?.innerHTML || "";
  const bodyMarkdown = htmlFragmentToMarkdown(bodyHtml, url);
  const articleMarkdown = article?.content
    ? htmlFragmentToMarkdown(article.content, url)
    : "";
  if (shouldUsePreferredContent(articleMarkdown, preferredMarkdown)) {
    return preferredMarkdown;
  }
  if (shouldUseBodyFallback(articleMarkdown, bodyMarkdown)) {
    return bodyMarkdown;
  }
  return articleMarkdown || preferredMarkdown || bodyMarkdown;
};

const errorMessage = (error) =>
  error && typeof error === "object" && "message" in error
    ? String(error.message)
    : String(error);

// Load the page in headless Chrome. Returns { finalUrl, title, markdown,
// screenshot?, screenshotError? }; markdown is omitted for screenshot mode.
const scrapeWithBrowser = async (url, outputMode) => {
  let page = null;
  try {
    const browserInstance = await getBrowser();
    page = await browserInstance.newPage();
    await preparePage(page, browserInstance);
    await attachPrivateRedirectGuard(page);

    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
    await settlePage(page);
    const finalUrl = page.url();
    if (!(await validateUrl(finalUrl))) {
      throw new Error("Redirected to invalid or private URL");
    }
    const title = await page.title();
    if (outputMode === "screenshot") {
      return { finalUrl, title, screenshot: await captureScreenshotSafe(page) };
    }

    let screenshot = null;
    let screenshotError = null;
    if (outputMode === "answer") {
      try {
        screenshot = await captureAnswerScreenshot(page);
      } catch (shotError) {
        screenshotError = errorMessage(shotError);
      }
    }

    let markdown = toMarkdown(await page.content(), finalUrl);
    if (textLimit && markdown.length > textLimit) {
      markdown = markdown.slice(0, textLimit);
    }
    const result = { finalUrl, title, markdown };
    if (outputMode === "answer") {
      result.screenshot = screenshot;
      result.screenshotContentType = "image/jpeg";
      result.screenshotError = screenshotError;
    }
    return result;
  } finally {
    if (page) {
      try {
        await page.close();
      } catch {
        // ignore
      }
    }
  }
};

// Per-host memory of whether raw HTML was enough or a browser was needed.
const hostModes = createHostModeCache();
const RAW_FILE_EXTENSION = /\.(txt|json|csv|tsv|md|xml|rss|atom|ya?ml|log)$/i;
// Static extraction must cover this share of what the browser rendered.
const STATIC_COVERAGE_THRESHOLD = 0.8;

const learnHostMode = (host, staticResult, browserResult) => {
  if (!browserResult?.markdown?.trim()) return;
  const coverage = staticResult?.markdown
    ? contentCoverage(staticResult.markdown, browserResult.markdown)
    : 0;
  const mode = coverage >= STATIC_COVERAGE_THRESHOLD ? "static" : "browser";
  hostModes.set(host, mode);
  console.info("Learned scrape mode", { host, mode, coverage: coverage.toFixed(2) });
};

app.post("/scrape", async (req, res) => {
  const { url, output, render } = req.body || {};
  if (!url || typeof url !== "string") {
    return res.status(400).json({ error: "Missing url" });
  }
  if (!url.startsWith("http://") && !url.startsWith("https://")) {
    return res.status(400).json({ error: "Invalid URL scheme" });
  }
  const requestedMode = typeof output === "string" ? output.toLowerCase() : "";
  // "answer" = markdown + screenshot from a single page load.
  const outputMode = ["screenshot", "answer"].includes(requestedMode)
    ? requestedMode
    : "markdown";
  const forceRender = render === true;

  try {
    const isValid = await validateUrl(url);
    if (!isValid) {
      return res.status(400).json({ error: "Invalid or private URL" });
    }
    const host = new URL(url).hostname.toLowerCase();

    let httpDoc = null;
    let httpError = null;
    const loadHttpDoc = async (timeoutMs = 8000) => {
      if (httpDoc || httpError) return httpDoc;
      try {
        httpDoc = await fetchDocument(url, { validateUrl, timeoutMs });
      } catch (error) {
        httpError = error;
      }
      return httpDoc;
    };
    const staticMarkdown = () =>
      httpDoc ? documentToMarkdown(httpDoc, { textLimit, toMarkdown }) : null;

    // Screenshots, or a full render the caller asked for, always use the browser.
    // Answer mode goes through the static/learned path below and only renders
    // (and screenshots) when the page actually needs a browser.
    if (outputMode === "screenshot" || forceRender) {
      try {
        const result = await scrapeWithBrowser(url, outputMode);
        if (outputMode !== "screenshot" && !result.markdown?.trim()) {
          await loadHttpDoc();
          const fallback = staticMarkdown();
          if (fallback?.markdown) result.markdown = fallback.markdown;
        }
        if (outputMode === "markdown" && !result.markdown?.trim()) {
          return res.status(422).json({
            error: "Scrape returned empty content",
            detail: `No extractable text from ${result.finalUrl || url}`,
            finalUrl: result.finalUrl,
            title: result.title,
          });
        }
        return res.json({ ...result, method: "browser" });
      } catch (browserError) {
        if (outputMode === "screenshot") throw browserError;
        await loadHttpDoc();
        const fallback = staticMarkdown();
        if (fallback?.markdown) {
          console.warn("Browser scrape failed, served HTTP fallback", {
            url,
            error: errorMessage(browserError),
          });
          return res.json({ ...fallback, method: "static-fallback" });
        }
        throw browserError;
      }
    }

    const knownMode = hostModes.get(host);

    // Unknown host: start the browser now so a wrong static guess costs no extra time.
    // Raw file URLs (.json, .txt, ...) never need it.
    const looksLikeRawFile = RAW_FILE_EXTENSION.test(new URL(url).pathname);
    const browserMode = outputMode === "answer" ? "answer" : "markdown";
    const browserPromise =
      knownMode === "static" || looksLikeRawFile ? null : scrapeWithBrowser(url, browserMode);
    browserPromise?.catch(() => {}); // handled below; avoid unhandled rejection

    if (knownMode !== "browser") {
      await loadHttpDoc();
      // Plain text / JSON / etc. never need a browser.
      if (httpDoc && isDirectTextDocument(httpDoc)) {
        const direct = documentToMarkdown(httpDoc, { textLimit });
        if (direct?.markdown) {
          return res.json({ ...direct, method: "static" });
        }
      }
    }

    const staticResult = staticMarkdown();
    const analysis = httpDoc ? analyzeStaticHtml(httpDoc.body) : null;
    const staticComplete =
      isUsableStaticMarkdown(staticResult) && analysis && !analysis.shell;

    if (knownMode === "static") {
      if (isUsableStaticMarkdown(staticResult)) {
        return res.json({ ...staticResult, method: "static" });
      }
      // Host normally works statically but this page didn't: re-learn it.
      hostModes.delete(host);
    } else if (knownMode !== "browser" && staticComplete) {
      // Confident static result: answer now, let the browser finish in the
      // background and record whether static was actually complete.
      res.json({ ...staticResult, method: "static" });
      browserPromise
        .then((browserResult) => learnHostMode(host, staticResult, browserResult))
        .catch((error) =>
          console.warn("Background browser comparison failed", {
            url,
            error: errorMessage(error),
          }),
        );
      return;
    }

    try {
      const browserResult = await (browserPromise || scrapeWithBrowser(url, browserMode));
      if (knownMode !== "browser") {
        learnHostMode(host, staticResult, browserResult);
      }
      if (browserResult.markdown?.trim()) {
        return res.json({ ...browserResult, method: "browser" });
      }
      // Soft-blocked / blank SPA shells: reuse the HTTP body when possible.
      await loadHttpDoc();
      const fallback = staticMarkdown();
      if (fallback?.markdown) {
        return res.json({ ...fallback, method: "static-fallback" });
      }
      return res.status(422).json({
        error: "Scrape returned empty content",
        detail: `No extractable text from ${browserResult.finalUrl || url}`,
        finalUrl: browserResult.finalUrl,
        title: browserResult.title,
      });
    } catch (browserError) {
      await loadHttpDoc();
      const fallback = staticMarkdown();
      if (fallback?.markdown) {
        console.warn("Browser scrape failed, served HTTP fallback", {
          url,
          error: errorMessage(browserError),
        });
        return res.json({ ...fallback, method: "static-fallback" });
      }
      throw browserError;
    }
  } catch (err) {
    const message = errorMessage(err);
    console.error("Scrape failed", { url, outputMode, error: message });
    if (res.headersSent) return;
    return res.status(500).json({ error: "Scrape failed", detail: message });
  }
});

app.get("/healthz", (_req, res) => {
  res.json({ ok: true });
});

app.post("/marp/export", async (req, res) => {
  const { markdown, format } = req.body || {};
  try {
    const result = await exportMarpPresentation({
      markdown,
      format: typeof format === "string" ? format.toLowerCase() : "pdf",
    });
    res.setHeader("Content-Type", result.contentType);
    res.setHeader("Cache-Control", "private, no-store");
    res.setHeader("X-Marp-Format", result.format);
    return res.status(200).send(result.buffer);
  } catch (error) {
    const message =
      error && typeof error === "object" && "message" in error
        ? String(error.message)
        : String(error);
    const status =
      error && typeof error === "object" && "status" in error
        ? Number(error.status) || 500
        : 500;
    console.error("marp export failed", { status, error: message });
    return res.status(status).json({ error: "Marp export failed", detail: message });
  }
});

app.listen(port, "0.0.0.0", () => {
  console.log(`scraper listening on ${port}`);
});
