import fs from "fs/promises";
import os from "os";
import path from "path";
import puppeteer from "puppeteer";
import { marpCli } from "@marp-team/marp-cli";

const MAX_MARKDOWN_BYTES = Number(process.env.MARP_EXPORT_MAX_MARKDOWN_BYTES || 12_000_000);
const BROWSER_TIMEOUT_SEC = Number(process.env.MARP_EXPORT_BROWSER_TIMEOUT || 120);

const resolveBrowserPath = async () => {
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    return process.env.PUPPETEER_EXECUTABLE_PATH;
  }
  if (process.env.CHROME_PATH) {
    return process.env.CHROME_PATH;
  }
  try {
    const pathOrPromise = puppeteer.executablePath();
    return await pathOrPromise;
  } catch {
    return null;
  }
};

/**
 * Convert Marp markdown to PDF or PPTX via official marp-cli + Chromium.
 */
export const exportMarpPresentation = async ({
  markdown,
  format = "pdf",
}) => {
  if (typeof markdown !== "string" || !markdown.trim()) {
    const error = new Error("Missing markdown");
    error.status = 400;
    throw error;
  }
  const byteLength = Buffer.byteLength(markdown, "utf8");
  if (byteLength > MAX_MARKDOWN_BYTES) {
    const error = new Error(
      `Markdown exceeds maximum size (${MAX_MARKDOWN_BYTES} bytes)`,
    );
    error.status = 413;
    throw error;
  }

  const normalizedFormat = format === "pptx" ? "pptx" : "pdf";
  const browserPath = await resolveBrowserPath();
  if (!browserPath) {
    const error = new Error("Chrome/Chromium executable not found for Marp export");
    error.status = 503;
    throw error;
  }

  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "marp-export-"));
  const inputPath = path.join(dir, "deck.md");
  const outputPath = path.join(dir, `deck.${normalizedFormat}`);

  try {
    await fs.writeFile(inputPath, markdown, "utf8");
    const args = [
      inputPath,
      normalizedFormat === "pptx" ? "--pptx" : "--pdf",
      "-o",
      outputPath,
      "--browser",
      "chrome",
      "--browser-path",
      browserPath,
      "--browser-timeout",
      String(BROWSER_TIMEOUT_SEC),
      // Data URLs / remote attachment URLs are fine; allow local if images were staged.
      "--allow-local-files",
      "--html",
    ];

    const exitStatus = await marpCli(args);
    if (exitStatus > 0) {
      const error = new Error(`Marp CLI failed with exit status ${exitStatus}`);
      error.status = 500;
      throw error;
    }

    const buffer = await fs.readFile(outputPath);
    if (!buffer.length) {
      const error = new Error("Marp CLI produced an empty file");
      error.status = 500;
      throw error;
    }

    return {
      format: normalizedFormat,
      contentType:
        normalizedFormat === "pptx"
          ? "application/vnd.openxmlformats-officedocument.presentationml.presentation"
          : "application/pdf",
      buffer,
    };
  } finally {
    await fs.rm(dir, { recursive: true, force: true }).catch(() => {});
  }
};
