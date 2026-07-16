import express from "express";
import puppeteer from "puppeteer";
import { Readability } from "@mozilla/readability";
import { JSDOM } from "jsdom";
import TurndownService from "turndown";
import ipaddr from "ipaddr.js";
import { URL } from "url";
import dns from "dns/promises";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const app = express();
app.use(express.json({ limit: "1mb" }));

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

const isPrivateIP = (ip) => {
  try {
    const parsed = ipaddr.parse(ip);
    const range = parsed.range();
    return (
      range === "private" ||
      range === "loopback" ||
      range === "linkLocal" ||
      range === "reserved"
    );
  } catch {
    return false;
  }
};

const validateUrl = async (urlString) => {
  try {
    const url = new URL(urlString);
    if (!["http:", "https:"].includes(url.protocol)) {
      return false;
    }
    const hostname = url.hostname;
    // Check if hostname is an IP
    if (ipaddr.isValid(hostname)) {
      if (isPrivateIP(hostname)) {
        return false;
      }
    } else {
      // Resolve DNS
      const addresses = await dns.resolve(hostname);
      for (const ip of addresses) {
        if (isPrivateIP(ip)) {
          return false;
        }
      }
    }
    return true;
  } catch {
    return false;
  }
};

const getBrowser = async () => {
  if (!browser) {
    browser = await puppeteer.launch({
      args: ["--no-sandbox", "--disable-setuid-sandbox"],
    });
  }
  return browser;
};

const DEFAULT_VIEWPORT = { width: 1366, height: 1800 };

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const settlePage = async (page) => {
  // Let SPA routes, deferred scripts, and hydration settle.
  try {
    await page.waitForFunction(() => document.readyState === "complete", {
      timeout: 8000,
    });
  } catch {}

  try {
    await page.waitForNetworkIdle({ idleTime: 700, timeout: 8000 });
  } catch {}

  // Trigger lazy loaders (IntersectionObserver/image/content reveal).
  try {
    await page.evaluate(async () => {
      const totalHeight = Math.max(
        document.body?.scrollHeight || 0,
        document.documentElement?.scrollHeight || 0,
      );
      const viewport = window.innerHeight || 900;
      let y = 0;
      while (y < totalHeight) {
        window.scrollTo(0, y);
        y += Math.max(Math.floor(viewport * 0.9), 400);
        // Small delay between scroll steps to allow lazy content to render.
        await new Promise((resolve) => setTimeout(resolve, 120));
      }
      window.scrollTo(0, 0);
    });
  } catch {}

  // One final short settle after scrolling.
  await sleep(250);
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

app.post("/scrape", async (req, res) => {
  const { url, output } = req.body || {};
  if (!url || typeof url !== "string") {
    return res.status(400).json({ error: "Missing url" });
  }
  if (!url.startsWith("http://") && !url.startsWith("https://")) {
    return res.status(400).json({ error: "Invalid URL scheme" });
  }
  const outputMode =
    typeof output === "string" && output.toLowerCase() === "screenshot"
      ? "screenshot"
      : "markdown";

  try {
    const isValid = await validateUrl(url);
    if (!isValid) {
      return res.status(400).json({ error: "Invalid or private URL" });
    }

    const browserInstance = await getBrowser();
    const page = await browserInstance.newPage();
    await page.setViewport(DEFAULT_VIEWPORT);

    // Enable request interception to block private IPs during navigation
    await page.setRequestInterception(true);
    page.on("request", async (request) => {
      if (request.isNavigationRequest() && request.redirectChain().length > 0) {
        const targetUrl = request.url();
        const valid = await validateUrl(targetUrl);
        if (!valid) {
          request.abort();
          return;
        }
      }
      request.continue();
    });

    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 20000 });
    await settlePage(page);
    const finalUrl = page.url();
    const title = await page.title();
    if (outputMode === "screenshot") {
      const screenshot = await captureScreenshotSafe(page);
      await page.close();
      return res.json({ finalUrl, title, screenshot });
    }

    const html = await page.content();
    await page.close();

    let markdown = toMarkdown(html, finalUrl);
    if (textLimit && markdown.length > textLimit) {
      markdown = markdown.slice(0, textLimit);
    }

    return res.json({ finalUrl, title, markdown });
  } catch (err) {
    const message =
      err && typeof err === "object" && "message" in err
        ? String(err.message)
        : String(err);
    console.error("Scrape failed", { url, outputMode, error: message });
    return res.status(500).json({ error: "Scrape failed", detail: message });
  }
});

app.get("/healthz", (_req, res) => {
  res.json({ ok: true });
});

app.listen(port, "0.0.0.0", () => {
  console.log(`scraper listening on ${port}`);
});
