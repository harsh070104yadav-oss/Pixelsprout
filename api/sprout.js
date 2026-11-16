// /api/sprout - anonymous player progress (Sprout XP, quests, weekly league,
// favourites, recently played).
//
// Identity: a random 256-bit token in a first-party cookie "ps_pid"
// (HttpOnly, Secure, SameSite=Lax). The database only ever sees its SHA-256
// hash. No login, no personal data, no fingerprinting. Clearing cookies,
// private windows, another browser or another device = a new anonymous
// player; nothing tries to link them.
//
// XP is never accepted from the browser. The browser reports two facts -
// "this game page was opened" and "this game was actively played for 60 s" -
// and the server checks the 60 s against the database clock, then decides
// every award itself through the idempotent ledger (sprout_award).
//
//   GET  /api/sprout?lb=0|3|10          progress (+ optional league rows)
//   POST {action:"open", slug}          game page opened (starts server timer)
//   POST {action:"qualify", slug}       >= 60 s of active play reported
//   POST {action:"favorite", slug, on}  add/remove favourite
//   POST {action:"import", favorites, recent}  one-time localStorage import
//   POST {action:"forget", optout}      delete all progress (+ stop tracking)
//   POST {action:"resume"}              undo opt-out
import crypto from "node:crypto";
import { supabaseConfigured, rpcJson } from "./_supabase.js";
import { clientIp, netBucket } from "./cold-read-dataset.js";
import { SPROUT, levelFor } from "./_sprout-config.js";
import { dueAwards, progressView, leaderboardView } from "./_sprout-core.js";
import { GAME_CATEGORIES } from "./_sprout-games.js";

export const config = { api: { bodyParser: false } };

const COOKIE = "ps_pid";
const OPTOUT_COOKIE = "ps_noid";
const COOKIE_MAX_AGE = 400 * 86400; // browsers cap cookie lifetime at 400 days
const TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;
const SLUG_RE = /^[a-z0-9-]{1,120}$/;
const MAX_BODY_BYTES = 8192;
const ACTIONS = {
  open: ["action", "slug"],
  qualify: ["action", "slug"],
  favorite: ["action", "slug", "on"],
  import: ["action", "favorites", "recent"],
  forget: ["action", "optout"],
  resume: ["action"],
};

class HttpError extends Error {
  constructor(status, msg) { super(msg); this.status = status; }
}

// ---------------------------------------------------------------- cookies --

export function parseCookies(header) {
  const out = {};
  String(header || "").split(";").forEach((part) => {
    const i = part.indexOf("=");
    if (i < 0) return;
    const k = part.slice(0, i).trim();
    if (k && !(k in out)) out[k] = part.slice(i + 1).trim();
  });
  return out;
}

function cookieAttrs(maxAge) {
  return `Path=/; Max-Age=${maxAge}; HttpOnly; Secure; SameSite=Lax`;
}

export function playerKey(token) {
  return crypto.createHash("sha256").update(token).digest("hex");
}

// Resolves (or issues) the anonymous identity for this request.
function identity(req, res, { issue }) {
  const cookies = parseCookies(req.headers.cookie);
  if (cookies[OPTOUT_COOKIE] === "1") return { optout: true, key: null, issued: false };
  let token = cookies[COOKIE];
  let issued = false;
  if (!token || !TOKEN_RE.test(token)) {
    if (!issue) return { optout: false, key: null, issued: false };
    token = crypto.randomBytes(32).toString("base64url");
    issued = true;
  }
  // Rolling expiry: re-set on every response so active players keep their ID.
  res.setHeader("Set-Cookie", `${COOKIE}=${token}; ${cookieAttrs(COOKIE_MAX_AGE)}`);
  return { optout: false, key: playerKey(token), issued };
}

// ------------------------------------------------------------ rate limits --

const memHits = new Map();
function memCount(bucket, windowStart) {
  const k = bucket + "@" + windowStart;
  const n = (memHits.get(k) || 0) + 1;
  memHits.set(k, n);
  if (memHits.size > 5000) for (const key of memHits.keys()) { if (!key.endsWith("@" + windowStart)) memHits.delete(key); }
  return n;
}

function h(s) { return crypto.createHash("sha256").update("sprout:" + s).digest("hex").slice(0, 32); }

async function rateLimit(req, key, isWrite) {
  const L = SPROUT.rateLimits;
  const buckets = [`n:${h(netBucket(clientIp(req)))}`];
  const limits = [L.perNetwork];
  if (key) { buckets.push(`p:${key.slice(0, 32)}`); limits.push(L.perPlayer); }
  if (key && isWrite) { buckets.push(`w:${key.slice(0, 32)}`); limits.push(L.perPlayerWrites); }
  let counts;
  try {
    counts = await rpcJson("sprout_rate_hit", { p_buckets: buckets, p_window_secs: L.windowSeconds });
    if (!Array.isArray(counts) || counts.length !== buckets.length) throw new Error("bad rate result");
  } catch (err) {
    console.error("sprout rate-limit store unavailable", String((err && err.message) || err).slice(0, 200));
    const ws = Math.floor(Date.now() / 1000 / L.windowSeconds);
    counts = buckets.map((b) => memCount(b, ws));
  }
  if (counts.some((c, i) => c > limits[i])) throw new HttpError(429, "Too many requests, slow down");
}

// ---------------------------------------------------------------- request --

async function readBody(req) {
  if (req.body && typeof req.body === "object" && !Buffer.isBuffer(req.body)) return req.body;
  const len = Number(req.headers["content-length"] || 0);
  if (len > MAX_BODY_BYTES) { if (typeof req.resume === "function") req.resume(); throw new HttpError(413, "payload too large"); }
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw new HttpError(413, "payload too large");
    chunks.push(chunk);
  }
  const raw = Buffer.concat(chunks).toString("utf8");
  if (!raw) throw new HttpError(400, "empty body");
  try { return JSON.parse(raw); } catch { throw new HttpError(400, "malformed JSON"); }
}

function isPlainObject(x) {
  return x !== null && typeof x === "object" && !Array.isArray(x) && Object.getPrototypeOf(x) === Object.prototype;
}

export function validateBody(body) {
  if (!isPlainObject(body)) throw new HttpError(400, "body must be a JSON object");
  const fields = ACTIONS[body.action];
  if (!fields) throw new HttpError(400, "unknown action");
  for (const k of Object.keys(body)) if (!fields.includes(k)) throw new HttpError(400, `unexpected field: ${String(k).slice(0, 40)}`);
  if (fields.includes("slug")) {
    if (typeof body.slug !== "string" || !SLUG_RE.test(body.slug)) throw new HttpError(400, "invalid slug");
    if (!Object.prototype.hasOwnProperty.call(GAME_CATEGORIES, body.slug)) throw new HttpError(404, "unknown game");
  }
  if (body.action === "favorite" && typeof body.on !== "boolean") throw new HttpError(400, "on must be boolean");
  if (body.action === "forget" && body.optout !== undefined && typeof body.optout !== "boolean") throw new HttpError(400, "optout must be boolean");
  if (body.action === "import") {
    for (const f of ["favorites", "recent"]) {
      const v = body[f];
      if (v === undefined) continue;
      if (!Array.isArray(v) || v.length > (f === "recent" ? 20 : 200)) throw new HttpError(400, `${f} must be a short array`);
      body[f] = v.filter((s) => typeof s === "string" && SLUG_RE.test(s) && Object.prototype.hasOwnProperty.call(GAME_CATEGORIES, s));
    }
  }
  return body;
}

// Blocks cross-site POSTs. SameSite=Lax already keeps the cookie off
// cross-site POSTs; this also rejects forged Origin headers outright.
function sameOrigin(req) {
  const origin = req.headers.origin;
  if (!origin) return true;
  try {
    const host = String(req.headers["x-forwarded-host"] || req.headers.host || "").split(",")[0].trim().toLowerCase();
    return new URL(origin).host.toLowerCase() === host;
  } catch { return false; }
}

// ----------------------------------------------------------------- actions --

async function loadProgress(key) {
  return rpcJson("sprout_state", { p_player: key });
}

// Progress for the browser, plus a short one-way "sync_id" the page uses to
// know whether it already imported its localStorage lists for this player.
// It is not the player ID and cannot be turned back into it.
function view(state, key) {
  const v = progressView(state);
  if (v.player && key) v.sync_id = crypto.createHash("sha256").update("sync:" + key).digest("hex").slice(0, 12);
  return v;
}

async function leaderboard(key, limit) {
  return leaderboardView(await rpcJson("sprout_leaderboard", { p_player: key || "", p_limit: limit }));
}

async function doQualify(key, slug) {
  const category = GAME_CATEGORIES[slug];
  const q = await rpcJson("sprout_qualify", { p_player: key, p_slug: slug, p_category: category, p_min_secs: SPROUT.qualifyingSeconds });
  if (!q || !q.ok) return { status: 409, body: { ok: false, reason: (q && q.reason) || "rejected" } };

  let state = await loadProgress(key);
  const before = levelFor(state.lifetime_xp);
  const awards = [];
  for (const a of dueAwards(state)) {
    const r = await rpcJson("sprout_award", { p_player: key, p_idem: a.idem, p_reason: a.reason, p_xp: a.xp, p_daily_cap: SPROUT.dailyXpCap });
    if (r && r.awarded) awards.push({ reason: a.reason, label: a.label, xp: r.xp, capped: Boolean(r.capped), key: a.idem.split(":").slice(0, 2).join(":") });
    else if (r && !r.duplicate && r.capped !== undefined) awards.push({ reason: a.reason, label: a.label, xp: 0, capped: true, key: a.idem.split(":").slice(0, 2).join(":") });
  }
  if (awards.length) state = await loadProgress(key);
  const after = levelFor(state.lifetime_xp);
  return {
    status: 200,
    body: {
      ok: true,
      new_session: Boolean(q.new_session),
      awards,
      level_up: after.level > before.level ? { from: before.name, to: after.name, level: after.level } : null,
      progress: view(state, key),
    },
  };
}

// ----------------------------------------------------------------- handler --

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("X-Content-Type-Options", "nosniff");
  if (req.method !== "GET" && req.method !== "POST") {
    res.setHeader("Allow", "GET, POST");
    res.status(405).json({ error: "Method not allowed" });
    return;
  }
  if (!supabaseConfigured()) {
    if (typeof req.resume === "function") req.resume();
    res.status(200).json({ enabled: false });
    return;
  }

  try {
    if (req.method === "GET") {
      const id = identity(req, res, { issue: true });
      if (id.optout) { res.status(200).json({ enabled: true, optout: true }); return; }
      await rateLimit(req, id.key, false);
      const lbLimit = Math.max(0, Math.min(SPROUT.leaderboardSize, parseInt((req.query && req.query.lb) || new URL(req.url, "http://x").searchParams.get("lb") || "0", 10) || 0));
      const [state, lb] = await Promise.all([
        id.issued ? Promise.resolve(null) : loadProgress(id.key),
        lbLimit ? leaderboard(id.issued ? "" : id.key, lbLimit) : Promise.resolve(null),
      ]);
      const progress = state ? view(state, id.key) : progressView({ exists: false, ...todayAndWeek() });
      res.status(200).json({ enabled: true, optout: false, progress, leaderboard: lb });
      return;
    }

    if (!sameOrigin(req)) throw new HttpError(403, "cross-origin request rejected");
    const ct = String(req.headers["content-type"] || "");
    if (!ct.startsWith("application/json")) throw new HttpError(415, "content-type must be application/json");
    const body = validateBody(await readBody(req));

    if (body.action === "resume") {
      res.setHeader("Set-Cookie", `${OPTOUT_COOKIE}=; ${cookieAttrs(0)}`);
      res.status(200).json({ ok: true });
      return;
    }

    if (body.action === "forget") {
      const id = identity(req, res, { issue: false });
      if (id.key) {
        await rateLimit(req, id.key, true);
        await rpcJson("sprout_forget", { p_player: id.key });
      }
      const cookies = [`${COOKIE}=; ${cookieAttrs(0)}`];
      if (body.optout) cookies.push(`${OPTOUT_COOKIE}=1; ${cookieAttrs(COOKIE_MAX_AGE)}`);
      res.setHeader("Set-Cookie", cookies);
      res.status(200).json({ ok: true, optout: Boolean(body.optout) });
      return;
    }

    const id = identity(req, res, { issue: body.action !== "qualify" });
    if (id.optout) { res.status(200).json({ ok: false, optout: true }); return; }
    if (!id.key) throw new HttpError(409, "no player");
    await rateLimit(req, id.key, true);

    if (body.action === "open") {
      const r = await rpcJson("sprout_open", { p_player: id.key, p_slug: body.slug });
      res.status(200).json({ ok: true, player_created: Boolean(r && r.created), qualifying_seconds: SPROUT.qualifyingSeconds });
      return;
    }
    if (body.action === "qualify") {
      const out = await doQualify(id.key, body.slug);
      res.status(out.status).json(out.body);
      return;
    }
    if (body.action === "favorite") {
      const r = await rpcJson("sprout_set_favorite", { p_player: id.key, p_slug: body.slug, p_on: body.on });
      res.status(r && r.ok ? 200 : 409).json(r || { ok: false });
      return;
    }
    if (body.action === "import") {
      await rpcJson("sprout_import", { p_player: id.key, p_favorites: body.favorites || [], p_recent: body.recent || [] });
      res.status(200).json({ ok: true, progress: view(await loadProgress(id.key), id.key) });
      return;
    }
    throw new HttpError(400, "unknown action");
  } catch (err) {
    if (err instanceof HttpError) {
      if (err.status === 429) res.setHeader("Retry-After", String(SPROUT.rateLimits.windowSeconds));
      res.status(err.status).json({ error: err.message });
      return;
    }
    console.error("sprout", String((err && err.stack) || err).slice(0, 500));
    res.status(500).json({ error: "Progress service unavailable" });
  }
}

function todayAndWeek() {
  const now = new Date();
  const day = now.toISOString().slice(0, 10);
  const dow = (now.getUTCDay() + 6) % 7; // Monday = 0
  const week = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - dow)).toISOString().slice(0, 10);
  return { day, week };
}
