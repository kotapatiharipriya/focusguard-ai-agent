// FocusGuard AI — site rules and classification.
// Shared by the background worker, content scripts and extension pages.
// PRIVACY: a URL becomes a coarse (app, surface) pair here. URLs, titles,
// video IDs and searches are never stored in stats or sent to the app.

/* exported DG_DEFAULT_RULES, dgClassify, dgSurface, dgShortKey, dgRuleIdForHost, dgNormalizeHost, dgMatchRule */

// mode: "off" (ignored) · "track" (measured) · "limit" (blocked after limitMin a day) · "block"
const DG_DEFAULT_RULES = [
  { id: "youtube_shorts",  label: "YouTube Shorts",  hosts: ["youtube.com"],   paths: ["/shorts"],          mode: "block", limitMin: 15, builtin: true },
  { id: "instagram_reels", label: "Instagram Reels", hosts: ["instagram.com"], paths: ["/reels", "/reel/"], mode: "block", limitMin: 15, builtin: true },
  { id: "tiktok",          label: "TikTok",          hosts: ["tiktok.com"],                                 mode: "block", limitMin: 15, builtin: true },
  { id: "instagram",       label: "Instagram",       hosts: ["instagram.com"],                              mode: "limit", limitMin: 15, builtin: true },
  { id: "youtube",         label: "YouTube",         hosts: ["youtube.com"],                                mode: "track", limitMin: 30, builtin: true },
  { id: "reddit",          label: "Reddit",          hosts: ["reddit.com"],                                 mode: "track", limitMin: 20, builtin: true },
  { id: "x",               label: "X",               hosts: ["x.com", "twitter.com"],                       mode: "track", limitMin: 20, builtin: true },
  { id: "facebook",        label: "Facebook",        hosts: ["facebook.com"],                               mode: "track", limitMin: 20, builtin: true },
];

function dgNormalizeHost(input) {
  let s = String(input || "").trim().toLowerCase();
  if (!s) return "";
  if (!/^[a-z]+:\/\//.test(s)) s = "https://" + s;
  try {
    const h = new URL(s).hostname.replace(/^(www\.|m\.)/, "");
    return /^[a-z0-9.-]+\.[a-z]{2,}$/.test(h) ? h : "";
  } catch (_) { return ""; }
}

// Ids double as the backend's "app" name, so keep them [a-z0-9_]{1,40}.
function dgRuleIdForHost(host) {
  return ("site_" + host.replace(/[^a-z0-9]+/g, "_")).slice(0, 40);
}

function hostMatches(host, ruleHost) {
  return host === ruleHost || host.endsWith("." + ruleHost);
}

// Most specific rule wins: a rule with a matching path beats a host-only rule.
function dgMatchRule(url, rules) {
  let u;
  try { u = new URL(url); } catch (_) { return null; }
  if (u.protocol !== "https:" && u.protocol !== "http:") return null;
  const host = u.hostname.toLowerCase().replace(/^(www\.|m\.)/, "");
  let hostOnly = null;
  for (const r of rules || DG_DEFAULT_RULES) {
    if (!r.hosts.some((h) => hostMatches(host, h))) continue;
    if (r.paths && r.paths.length) {
      if (r.paths.some((p) => u.pathname.startsWith(p))) return r;
    } else if (!hostOnly) {
      hostOnly = r;
    }
  }
  return hostOnly;
}

// Coarse "where on the site" label, for analytics only.
function dgSurface(url) {
  let u;
  try { u = new URL(url); } catch (_) { return "other"; }
  const p = u.pathname;
  if (/^\/(shorts|reels?)(\/|$)/.test(p) || /tiktok\.com$/.test(u.hostname)) return "shorts";
  if (p.startsWith("/watch")) return "watch";
  if (p.startsWith("/results") || p.startsWith("/search")) return "search";
  if (p.includes("/comments/") || p.includes("/status/")) return "thread";
  if (p.startsWith("/explore")) return "explore";
  if (p === "/" || p.startsWith("/feed") || p.startsWith("/home")) return "feed";
  return "other";
}

// url -> { app, label, mode, limitMin, surface } or null when untracked / off.
function dgClassify(url, rules) {
  const r = dgMatchRule(url, rules);
  if (!r || r.mode === "off") return null;
  return { app: r.id, label: r.label, mode: r.mode, limitMin: r.limitMin, surface: dgSurface(url) };
}

// Short-video identity for counting swipes. Only compared locally, never sent.
function dgShortKey(url) {
  try {
    const m = new URL(url).pathname.match(/^\/(shorts|reel|reels)\/([^/?#]+)/);
    return m ? m[2] : null;
  } catch (_) { return null; }
}

if (typeof module !== "undefined") {
  module.exports = { DG_DEFAULT_RULES, dgClassify, dgMatchRule, dgSurface, dgShortKey, dgNormalizeHost, dgRuleIdForHost };
}
