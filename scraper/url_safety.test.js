import assert from "node:assert/strict";
import {
  isBlockedHostnameLiteral,
  isPrivateIP,
  normalizeHostname,
  validateUrl,
} from "./url_safety.js";

assert.equal(normalizeHostname("Host.Docker.Internal."), "host.docker.internal");

assert.equal(isBlockedHostnameLiteral("host.docker.internal"), true);
assert.equal(isBlockedHostnameLiteral("gateway.docker.internal"), true);
assert.equal(isBlockedHostnameLiteral("app.localhost"), true);
assert.equal(isBlockedHostnameLiteral("svc.local"), true);
assert.equal(isBlockedHostnameLiteral("backend"), true);
assert.equal(isBlockedHostnameLiteral("example.com"), false);

assert.equal(isPrivateIP("127.0.0.1"), true);
assert.equal(isPrivateIP("10.0.0.1"), true);
assert.equal(isPrivateIP("192.168.1.1"), true);
assert.equal(isPrivateIP("169.254.169.254"), true);
assert.equal(isPrivateIP("100.64.0.1"), true);
assert.equal(isPrivateIP("8.8.8.8"), false);
assert.equal(isPrivateIP("::1"), true);

assert.equal(await validateUrl("http://host.docker.internal/"), false);
assert.equal(await validateUrl("http://127.0.0.1/"), false);
assert.equal(await validateUrl("http://10.1.2.3/"), false);
assert.equal(await validateUrl("http://169.254.169.254/latest/meta-data"), false);
assert.equal(await validateUrl("ftp://example.com/"), false);
assert.equal(await validateUrl("https://example.com/ok"), true);

console.log("url_safety.js tests passed");
