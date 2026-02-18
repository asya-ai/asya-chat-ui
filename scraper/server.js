import express from "express";
import puppeteer from "puppeteer";
import { Readability } from "@mozilla/readability";
import { JSDOM } from "jsdom";
import TurndownService from "turndown";

const app = express();
app.use(express.json({ limit: "1mb" }));

const port = process.env.SCRAPER_PORT || 3001;
const textLimit = Number(process.env.SCRAPE_TEXT_LIMIT || 20000);

let browser;

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
  const { url } = req.body || {};
  if (!url || typeof url !== "string") {
    return res.status(400).json({ error: "Missing url" });
  }
  if (!url.startsWith("http://") && !url.startsWith("https://")) {
    return res.status(400).json({ error: "Invalid URL scheme" });
  }

  try {
    const browserInstance = await getBrowser();
    const page = await browserInstance.newPage();
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 15000 });
    const finalUrl = page.url();
    const title = await page.title();
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
