import test from "node:test";
import assert from "node:assert/strict";
import {
  analyzeStaticHtml,
  contentCoverage,
  createHostModeCache,
} from "./static_detect.js";

const words = (n, word = "content") => Array.from({ length: n }, (_, i) => `${word}${i}`).join(" ");

test("server-rendered article is not a shell", () => {
  const html = `<html><body><nav>Home About</nav><main><h1>Title</h1><p>${words(300)}</p></main></body></html>`;
  const result = analyzeStaticHtml(html);
  assert.equal(result.shell, false, result.reasons.join(","));
});

test("empty React mount with SEO chrome is a shell", () => {
  const html = `<html><body><header>${words(150, "nav")}</header><div id="root"></div><footer>${words(100, "foot")}</footer><script src="/app.js"></script></body></html>`;
  const result = analyzeStaticHtml(html);
  assert.equal(result.shell, true);
  assert.ok(result.reasons.includes("empty-mount:#root"));
});

test("noscript warning marks a shell", () => {
  const html = `<html><body><noscript>You need to enable JavaScript to run this app.</noscript><main>${words(200)}</main></body></html>`;
  assert.ok(analyzeStaticHtml(html).reasons.includes("noscript-requires-js"));
});

test("short page without a main/article region is not trusted", () => {
  const html = `<html><body><div>${words(400)}</div></body></html>`;
  assert.ok(analyzeStaticHtml(html).reasons.includes("no-main-content"));
});

test("long server-rendered page without semantic regions is trusted", () => {
  const html = `<html><body><table><tr><td>${words(5000)}</td></tr></table></body></html>`;
  assert.equal(analyzeStaticHtml(html).shell, false);
});

test("script-heavy page with little text is a shell", () => {
  const html = `<html><body><main>${words(90)}</main><script>${"x".repeat(20000)}</script></body></html>`;
  assert.ok(analyzeStaticHtml(html).reasons.includes("script-heavy"));
});

test("coverage compares distinct words", () => {
  assert.equal(contentCoverage("alpha bravo charlie delta", "alpha bravo charlie delta"), 1);
  assert.equal(contentCoverage("alpha bravo", "alpha bravo charlie delta"), 0.5);
  assert.equal(contentCoverage("", "alpha bravo"), 0);
});

test("host mode cache expires and evicts", () => {
  let clock = 0;
  const cache = createHostModeCache({ ttlMs: 100, maxEntries: 2, now: () => clock });
  cache.set("a.com", "static");
  cache.set("b.com", "browser");
  cache.set("c.com", "static");
  assert.equal(cache.get("a.com"), null);
  assert.equal(cache.get("b.com"), "browser");
  clock = 150;
  assert.equal(cache.get("c.com"), null);
});
