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
    const parsed = ipaddr.parse(String(ip));
    // Only global unicast is allowed; everything else is treated as private/special-use.
    return parsed.range() !== "unicast";
  } catch {
    return true;
  }
};

const resolveAddresses = async (hostname) => {
  const addresses = [];
  try {
    addresses.push(...(await dns.resolve4(hostname)));
  } catch {
    // ignore ENODATA / ENOTFOUND for A
  }
  try {
    addresses.push(...(await dns.resolve6(hostname)));
  } catch {
    // ignore ENODATA / ENOTFOUND for AAAA
  }
  return addresses;
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
