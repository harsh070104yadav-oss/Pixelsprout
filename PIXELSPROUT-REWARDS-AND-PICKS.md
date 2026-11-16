# Sprout XP rewards + Pixelsprout Picks

Anonymous player progress (Sprout XP, levels, comeback rewards, quests, weekly
league, server-synced favourites and recently played) and Amazon affiliate
recommendations, built into the existing static site + Vercel functions +
Supabase setup. No login, no new framework, no build step.

## 1. One-time setup (required before it switches on)

1. **Run the SQL migration** in Supabase → SQL editor:
   `supabase/migrations/20261116_sprout_rewards.sql` (safe to re-run).
2. **No new environment variables.** `/api/sprout` reuses the existing
   `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` already set in Vercel.
3. Deploy (normal push). Until step 1 is done the API fails with a 500 and the
   site simply shows no progress UI; games, favourites (localStorage) and
   everything else keep working as before. If the env vars are missing the API
   returns `{"enabled":false}` and the UI stays hidden.

Quick check after deploy: open any game, play for a minute, then visit
`/rewards/`.

### Works before setup too (in-browser fallback)
If the API answers `{"enabled":false}` (keys missing) or errors (migration not
run, wrong key, outage), `assets/sprout/sprout.js` switches to an in-browser
engine with the same rules: XP, levels, comeback reward, weekly bonus, quests,
favourites and recently played keep working, saved in localStorage
(`pixelsprout_sprout_local_v1`). The only thing that needs the server is the
shared weekly league, which then says "opens soon". The server is retried
every 5 minutes, and once it answers it takes over automatically. Local XP is
**not** copied into the server ledger, because server XP must be validated
server-side. If you change rules in `api/_sprout-config.js`, update the
`Local` block in `sprout.js` to match.

## 2. How it works

### Identity
* First API call sets `ps_pid`: 256 random bits, `HttpOnly; Secure;
  SameSite=Lax; Path=/; Max-Age=400 days` (rolling). Page JavaScript cannot
  read it.
* The database stores only `sha256(token)`. No email, name, IP or fingerprint.
* A player row is created only on the first game open, favourite or import,
  never for a plain page view.
* Clearing cookies / private browsing / another browser or device = a new
  player. Nothing tries to link them.
* `/rewards/` has **Reset my progress** (deletes all rows) and **Reset and stop
  tracking** (also sets `ps_noid=1`, after which no ID is issued and nothing is
  stored; **Turn progress back on** undoes it).

### What counts as play
1. Game page loads → `POST {action:"open"}` stores the server time.
2. The browser counts **active seconds** only while the tab is visible, the
   window has focus, and the player is interacting (focus inside the game
   iframe, or input within the last 15 s).
3. At 60 active seconds → `POST {action:"qualify"}`. The database rejects it
   unless ≥ 60 s have passed since the open **on the database clock**.
4. One qualifying session per game per UTC day. Refreshing, reopening or
   replaying the same game earns nothing more.

### XP (server-side only, `api/_sprout-config.js`)
| Rule | XP | Idempotency key |
|---|---|---|
| First qualifying session ever | 10 | `first_session` |
| Qualifying session on a later UTC day | 25 | `return:<day>` |
| Qualifying sessions on 3 different days in a week | 75 | `weekly_return:<week>` |
| Daily quests: new game 15, 2 categories 15, one session 10 | config | `dq:<id>:<day>` |
| Weekly quests: 5 games 60, 3 categories 50, 3 days 40 | config | `wq:<id>:<week>` |

* XP is written only by `sprout_award()`: unique `(player_key, idem_key)`,
  player row lock, daily cap (300) enforced inside the transaction.
* The browser never sends XP, categories or totals. Categories come from
  `api/_sprout-games.js`, generated from `games-index.json` by
  `generate-homepage.py` (already in the daily workflow).
* Levels by lifetime XP: Seed 0, Sprout 50, Sapling 150, Bloom 350, Canopy 700,
  Evergreen 1200. Lifetime XP never resets.
* All boundaries are UTC; the week and the Sprout League reset Monday 00:00 UTC.
  Missing days never removes anything (no streaks).

### Abuse protection
* Rate limits per player (40 req/min, 20 writes/min) and per hashed network
  (/24 or /48, 120 req/min), stored in `sprout_rate`, with an in-memory
  fallback if the store is unreachable.
* Strict body validation (allow-listed fields, slug format, catalog check,
  8 KB limit), `application/json` required, cross-origin `Origin` rejected,
  `SameSite=Lax` cookie.
* Imported localStorage history is back-dated and can never satisfy the timer.
* RLS on, no policies, all tables and functions revoked from `anon` /
  `authenticated`; only `service_role` (server only) can call them.
* Leaderboard exposes display name, weekly XP, level name and rank only.

### Favourites / recently played
* Server lists are the source of truth (`sprout_favorites`, `sprout_recent`,
  latest 20). The existing localStorage keys remain as a display cache, so the
  existing heart button, panels and search palette work unchanged.
* Existing localStorage favourites/history are merged into the server once per
  player (`sync_id`, a 12-char one-way hash, tracks this).

### Pixelsprout Picks
* Catalogue: `assets/picks/picks.json`. Pages: homepage row, `/picks/`, and one
  contextual card under the description on game pages (not on Kids,
  Educational or Math games).
* Ships with **category cards only** (Amazon search links tagged with your
  existing IDs `pixelsprout0e-21` / `pixelsprout-20`). No products, ASINs,
  prices, ratings, stock or images were invented.
* Store toggle Amazon.in / Amazon.com (India time zone defaults to .in).
* Every link: direct Amazon URL (no redirect/cloaking), `rel="sponsored
  noopener"`, new tab, "(paid link)" label, disclosure "As an Amazon Associate
  I earn from qualifying purchases." in the same section.
* `/picks/` is `noindex, follow` while it has no verified products. Set
  `"indexable": true` in `picks.json` once real products are added if you want
  it indexed (and add it to `sitemap.xml`).

**Adding a verified product** (to `products` in `picks.json`):
```json
{ "id": "my-mouse", "name": "Exact name as on Amazon", "category": "mice",
  "description": "Short, honest reason it is good for browser games.",
  "asin": "B0XXXXXXXX", "store": "IN", "verified_on": "2026-11-16",
  "image": "" }
```
`image` may only be an `https://m.media-amazon.com/...` URL obtained from
SiteStripe or the Product Advertising API (anything else is ignored and the
category illustration is shown). Never add price, rating, discount or stock
fields. Then run `python generate-homepage.py` (or wait for the daily run).

## 3. Analytics (GA4)

No player ID, display name or personal data is ever sent to GA4. GA4 still
loads on first interaction / after 8 s exactly as before; events are queued
until it is ready.

| Event | When | Main params |
|---|---|---|
| `anonymous_player_created` | first game open creates the anonymous player row | – |
| `game_start` | first real interaction with the game (click/focus into the iframe, key, touch) – **not** a page view | game_title, game_category |
| `game_loaded` | the game iframe's `load` event (when measurable) | game_title, game_category |
| `game_active_60s` | 60 active, visible seconds | game_title, game_category |
| `xp_awarded` | server confirmed an award | xp_amount, xp_reason |
| `quest_completed` | quest award | quest_id, quest_period, xp_amount |
| `return_qualifying_visit` | comeback award | game_category |
| `weekly_return_bonus_awarded` | weekly bonus | xp_amount |
| `leaderboard_viewed` | league visible (home preview / rewards page) | leaderboard_view |
| `badge_unlocked` | level up (badge = level name) | badge_name, badge_level |
| `affiliate_recommendation_impression` | a Picks block becomes visible | source_page, game_category, item_count, placement |
| `affiliate_product_view` | a pick card becomes visible | product_category, source_page, game_category, placement |
| `affiliate_link_click` | click on a paid link | product_category, link_type, store, source_page, game_category, placement |

* **Favourites:** the site already sends `favorite_add` / `favorite_remove`.
  These are kept as-is and are the "favorite_added / favorite_removed"
  measurement; no duplicate events were added.
* Page views are unchanged and are separate from `game_start`.
* Consider registering `game_category`, `xp_reason`, `quest_id`,
  `product_category`, `source_page`, `placement` as custom dimensions in GA4.
* Affiliate events count impressions and clicks only. Purchases and
  commissions must come from Amazon Associates reports.

## 4. Known limitations
* **Embedded games are cross-origin.** We cannot see inside them. `game_loaded`
  proves the frame loaded, not that the game works; "active play" means the
  player clicked into the game and the tab stayed visible and focused. A player
  who clicks into a game and leaves it idle in a visible, focused tab can still
  reach 60 s.
* Someone scripting requests could earn the same bounded XP a real player
  could (one session per game per day, daily cap, rate limits). There is no
  CAPTCHA or bot detection; the league is for fun, with no prizes.
* Identity is per browser profile by design.
* The `sprout_rate` table is swept opportunistically (rows > 2 h old).
* `sprout_sessions` keeps one row per player/game/day indefinitely (needed for
  "never played before"). Consider deleting players inactive for > 400 days.
* Games added outside `generate-homepage.py` earn no XP until the next daily
  run regenerates `api/_sprout-games.js`.

## 5. Amazon Associates items to verify manually
The official Associates pages could not be fetched from the build environment,
so please check these against the **current** Amazon.in Operating Agreement and
Program Policies:
1. Disclosure wording and placement ("As an Amazon Associate I earn from
   qualifying purchases." shown in each Picks block, on `/picks/`, under Guess
   It Box results and in the privacy policy).
2. That tagged **search-result links** (`/s?k=...&tag=...`) are acceptable
   Special Links for your account, or replace them with SiteStripe /
   PA-API links.
3. Your India account status (the 3-sales/180-day requirement) and the US
   account / tax interview, since the US tag is used for non-India visitors.
4. Whether your site must be registered for both `www.playpixelsprout.com` and
   `gifts.playpixelsprout.com`.
5. Rules for sites with child audiences: Picks are hidden on Kids/Educational/
   Math games, but the site as a whole has kids categories.
6. Mobile: links open the Amazon website in a new tab (no app deep links).
7. **Guess It Box changed:** it previously had no disclosure and used
   `rel="noreferrer"`. It now shows the disclosure and "(paid link)", and uses
   `rel="sponsored noopener"`.
