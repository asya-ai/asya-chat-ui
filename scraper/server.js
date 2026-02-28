import express from "express";
import puppeteer from "puppeteer";
import { Readability } from "@mozilla/readability";
import { JSDOM } from "jsdom";
import TurndownService from "turndown";
import ipaddr from "ipaddr.js";
import { URL } from "url";
import dns from "dns/promises";

const app = express();
app.use(express.json({ limit: "1mb" }));

const port = process.env.SCRAPER_PORT || 3001;
const textLimit = Number(process.env.SCRAPE_TEXT_LIMIT || 20000);

let browser;

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

const toMarkdown = (html, url) => {
  const dom = new JSDOM(html, { url });
  const reader = new Readability(dom.window.document);
  const article = reader.parse();
  const content = article?.content || dom.window.document.body.innerHTML || "";
  const turndown = new TurndownService({ headingStyle: "atx" });
  return turndown.turndown(content);
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

    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 15000 });
    const finalUrl = page.url();
    const title = await page.title();
    if (outputMode === "screenshot") {
      const screenshot = await page.screenshot({
        type: "png",
        fullPage: true,
        encoding: "base64",
      });
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
    return res.status(500).json({ error: "Scrape failed" });
  }
});

app.get("/healthz", (_req, res) => {
  res.json({ ok: true });
});

app.listen(port, "0.0.0.0", () => {
  console.log(`scraper listening on ${port}`);
});
