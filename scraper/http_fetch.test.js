import assert from "node:assert/strict";
import {
  CHROME_LAUNCH_ARGS,
  contentTypeBase,
  documentToMarkdown,
  isDirectTextDocument,
  isHtmlContentType,
  isPlainTextContentType,
} from "./http_fetch.js";

assert.equal(contentTypeBase("text/plain; charset=utf-8"), "text/plain");
assert.equal(isPlainTextContentType("text/plain; charset=utf-8"), true);
assert.equal(isPlainTextContentType("application/json"), true);
assert.equal(isPlainTextContentType("text/html"), false);
assert.equal(isHtmlContentType("text/html; charset=utf-8"), true);

assert.equal(
  isDirectTextDocument({
    contentType: "text/plain",
    body: "# hello",
  }),
  true,
);
assert.equal(
  isDirectTextDocument({
    contentType: "text/html",
    body: "<html><body>hi</body></html>",
  }),
  false,
);

const textDoc = documentToMarkdown(
  {
    finalUrl: "https://example.com/readme.md",
    contentType: "text/markdown",
    body: "# Title\n\nHello",
    title: "readme.md",
  },
  { textLimit: 100 },
);
assert.equal(textDoc.markdown, "# Title\n\nHello");

const htmlDoc = documentToMarkdown(
  {
    finalUrl: "https://example.com/page",
    contentType: "text/html",
    body: "<html><head><title>Hi</title></head><body><main><p>Hello world content here</p></main></body></html>",
    title: "Hi",
  },
  {
    toMarkdown: (html) => (html.includes("Hello world") ? "Hello world content here" : ""),
  },
);
assert.equal(htmlDoc.markdown, "Hello world content here");

assert.ok(CHROME_LAUNCH_ARGS.includes("--disable-dev-shm-usage"));
assert.ok(CHROME_LAUNCH_ARGS.includes("--no-sandbox"));

console.log("http_fetch.js tests passed");
