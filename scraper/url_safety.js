import dns from "dns/promises";
import ipaddr from "ipaddr.js";
import { URL } from "url";

const BLOCKED_HOSTNAMES = new Set([
  "localhost",
  "metadata",
  "metadata.google.internal",
  "host.docker.internal",
  "gateway.docker.internal",
  "kubernetes.docker.internal",
  "127.0.0.1",
  "0.0.0.0",
  "::1",
]);

const BLOCKED_SUFFIXES = [".local", ".internal", ".localhost", ".lan", ".home", ".corp"];

export const normalizeHostname = (hostname) =>
  String(hostname || "")
    .trim()
    .replace(/\.+$/, "")
    .toLowerCase();

export const isBlockedHostnameLiteral = (hostname) => {
  const host = normalizeHostname(hostname);
  if (!host) return true;
  if (BLOCKED_HOSTNAMES.has(host)) return true;
  if (BLOCKED_SUFFIXES.some((suffix) => host.endsWith(suffix))) return true;
  // Docker compose / intranet short names.
  if (!host.includes(".")) return true;
  return false;
};

/** True for any non-global address (RFC1918, loopback, link-local, CGNAT, ULA, …). */
export const isPrivateIP = (ip) => {
  try {
    let parsed = ipaddr.parse(String(ip));
    if (parsed.kind() === "ipv6" && parsed.isIPv4MappedAddress?.()) {
      parsed = parsed.toIPv4Address();
    }
    // Only global unicast is allowed; everything else is treated as private/special-use.
    return parsed.range() !== "unicast";
  } catch {
    return true;
  }
};

const resolveAddresses = async (hostname) => {
  // Prefer system getaddrinfo (dns.lookup) so Docker embedded DNS / hosts files
  // work the same way the HTTP client will when fetching.
  try {
    const result = await dns.lookup(hostname, { all: true, verbatim: true });
    return result.map((entry) => entry.address).filter(Boolean);
  } catch {
    return [];
  }
};

export const validateUrl = async (urlString) => {
  try {
    const url = new URL(urlString);
    if (!["http:", "https:"].includes(url.protocol)) {
      return false;
    }
    const hostname = normalizeHostname(url.hostname);
    if (isBlockedHostnameLiteral(hostname)) {
      return false;
    }
    if (ipaddr.isValid(hostname)) {
      return !isPrivateIP(hostname);
    }
    const addresses = await resolveAddresses(hostname);
    if (!addresses.length) {
      return false;
    }
    for (const ip of addresses) {
      if (isPrivateIP(ip)) {
        return false;
      }
    }
    return true;
  } catch {
    return false;
  }
};
