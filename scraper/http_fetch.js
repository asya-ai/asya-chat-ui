export const BROWSER_USER_AGENT =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

export const CHROME_LAUNCH_ARGS = [
  "--no-sandbox",
  "--disable-setuid-sandbox",
  // Critical in Docker: default /dev/shm is 64MB and Chrome tabs die silently.
  "--disable-dev-shm-usage",
  "--disable-gpu",
  "--no-zygote",
  "--disable-extensions",
  "--disable-background-networking",
  "--disable-default-apps",
  "--disable-sync",
  "--disable-translate",
  "--metrics-recording-only",
  "--mute-audio",
  "--no-first-run",
  "--safebrowsing-disable-auto-update",
  "--font-render-hinting=none",
];

export const contentTypeBase = (contentType) =>
  String(contentType || "")
    .split(";", 1)[0]
    .trim()
    .toLowerCase();

export const isHtmlContentType = (contentType) => {
  const type = contentTypeBase(contentType);
  return type === "text/html" || type === "application/xhtml+xml";
};

export const isPlainTextContentType = (contentType) => {
  const type = contentTypeBase(contentType);
  return (
    (type.startsWith("text/") && type !== "text/html") ||
    type === "application/json" ||
    type === "application/xml" ||
    type === "application/javascript" ||
    type === "application/yaml" ||
    type === "application/x-yaml" ||
    type === "application/toml" ||
    type.endsWith("+json") ||
    type.endsWith("+xml")
  );
};

export const filenameFromUrl = (urlString) => {
  try {
    const pathname = new URL(urlString).pathname;
    const name = decodeURIComponent(pathname.split("/").filter(Boolean).pop() || "");
    return name || "file";
  } catch {
    return "file";
  }
};

const titleFromHtml = (html) => {
  const match = String(html || "").match(/<title[^>]*>([^<]*)<\/title>/i);
  return match ? String(match[1] || "").trim() : "";
};

const looksLikeHtml = (body) => /<html[\s>]|<body[\s>]|<div[\s>]|<p[\s>]/i.test(body);

const DEFAULT_FETCH_HEADERS = {
  "User-Agent": BROWSER_USER_AGENT,
  "Accept-Language": "en-US,en;q=0.9",
  Accept:
    "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.7",
};

/**
 * Fetch a URL once. Caller decides whether to render with a browser or convert
 * the body directly based on content-type / body shape.
 */
export const fetchDocument = async (urlString, { timeoutMs = 20000 } = {}) => {
  const response = await fetch(urlString, {
    redirect: "follow",
    headers: DEFAULT_FETCH_HEADERS,
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!response.ok) {
    throw new Error(`HTTP fetch failed (${response.status}) for ${urlString}`);
  }
  const finalUrl = response.url || urlString;
  const contentType = response.headers.get("content-type") || "";
  const body = await response.text();
  if (!String(body || "").trim()) {
    throw new Error(`HTTP fetch returned empty body for ${finalUrl}`);
  }
  return {
    finalUrl,
    contentType,
    body,
    title: titleFromHtml(body) || filenameFromUrl(finalUrl),
  };
};

export const isDirectTextDocument = (doc) => {
  if (!doc) return false;
  if (isPlainTextContentType(doc.contentType)) return true;
  if (isHtmlContentType(doc.contentType)) return false;
  // Some hosts omit/mislabel content-type for raw files.
  return !looksLikeHtml(doc.body);
};

/**
 * Convert an already-fetched document into markdown when a browser is not needed
 * (plain text) or as a fallback for static HTML.
 */
export const documentToMarkdown = (doc, { textLimit, toMarkdown } = {}) => {
  if (!doc?.body) return null;

  if (isDirectTextDocument(doc)) {
    let markdown = String(doc.body);
    if (textLimit && markdown.length > textLimit) {
      markdown = markdown.slice(0, textLimit);
    }
    return {
      finalUrl: doc.finalUrl,
      title: doc.title || filenameFromUrl(doc.finalUrl),
      markdown,
    };
  }

  if (!looksLikeHtml(doc.body) && !isHtmlContentType(doc.contentType)) {
    return null;
  }
  if (typeof toMarkdown !== "function") {
    return null;
  }
  let markdown = toMarkdown(doc.body, doc.finalUrl);
  if (!String(markdown || "").trim()) {
    return null;
  }
  if (textLimit && markdown.length > textLimit) {
    markdown = markdown.slice(0, textLimit);
  }
  return {
    finalUrl: doc.finalUrl,
    title: doc.title || filenameFromUrl(doc.finalUrl),
    markdown,
  };
};
