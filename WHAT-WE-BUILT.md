# Pixelsprout retention & monetisation layer: what was built

One page summary. Details are in `PIXELSPROUT-REWARDS-AND-PICKS.md`
(rewards, league, Picks, Amazon) and `ENGAGEMENT.md` (difficulty, continues,
streaks, recommendations).

## The player journey it creates
1. **Arrives:** plays immediately. No login, popup or onboarding.
2. **Plays:** after 60 s of real, visible play → first Sprout XP + quest toasts in the corner.
3. **Fails in an original:**
   - near their best, an optional "Continue this run?" card;
   - difficulty quietly eases if they keep failing fast, and rises if they are cruising.
4. **Looks around:** after 2.5 min or a game over, "Because you played X" suggests the next game.
5. **Comes back another day:** "🌱 Welcome back to Pixelsprout! +25 Sprout XP", a streak chip, Continue Playing, favourites, best scores and fresh daily quests.
6. **Returns 3 days in a week:** +75 bonus, and climbs the weekly Sprout League.
7. **Optionally:** discovers gear through Pixelsprout Picks (Amazon paid links).

## Pieces

| Area | Files | Data lives in |
|---|---|---|
| Anonymous identity, Sprout XP, levels, comebacks, quests, league, synced favourites/recent | `api/sprout.js`, `api/_sprout-*.js`, `assets/sprout/*`, `rewards/`, `supabase/migrations/20261116_sprout_rewards.sql` | Supabase (hashed cookie ID). Falls back to the browser until the server is set up |
| Engagement: run history, difficulty 0.85-1.15x, continue offers, streak, best scores, recommendations | `assets/engagement/*`, hooks in `games/dewlane.js`, `games/moonbloom.p0/p1/p3` | Browser only |
| Pixelsprout Picks (Amazon) | `assets/picks/picks.json`, `picks/`, homepage row, game-page card | Static config |
| Homepage sections | `sprout_build.py`, `site_pages.py` (generated daily) | – |
| Privacy | `privacy.html` (new sections), rewards page controls | – |
| Compliance fix | `gift-file/index.html`: Amazon disclosure + "(paid link)" | – |
| Example | `examples/engagement-runner.html` (noindex) | – |

## Status right now
* **Live without any setup:**
  * Picks
  * continue offers
  * difficulty
  * streaks, best scores and recommendations
  * XP, levels, quests, comebacks, favourites and recently played (in-browser mode)
* **Needs your setup to go server-side** (validated XP, the public weekly league, progress kept even if local storage is wiped):
  1. Vercel → Settings → Environment Variables: `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` (Production), then Redeploy.
  2. Supabase → SQL Editor: run `supabase/migrations/20261116_sprout_rewards.sql`.
  3. `https://www.playpixelsprout.com/api/sprout` should then return `{"enabled":true,...}`.

## Measuring retention (GA4)
Compare 4 weeks before vs after launch:
* Returning users and user stickiness (DAU/MAU): Reports → Retention.
* `game_start` per user, and `game_active_60s` ÷ `game_start` (real play vs bounce).
* `return_qualifying_visit`, `weekly_return_bonus_awarded`: comebacks.
* `quest_completed`, `xp_awarded`, `badge_unlocked`, `leaderboard_viewed`.
* `continue_offered` vs `continue_accepted`, and `game_retry`: originals' replay loop.
* `affiliate_link_click` by `product_category` / `source_page`. Earnings come from Amazon Associates reports only.

Register `game_category`, `xp_reason`, `quest_id`, `product_category`,
`source_page`, `placement` as custom dimensions so you can slice by them.

## Tuning
* XP, quests, caps, levels: `api/_sprout-config.js` (+ the `Local` block in `assets/sprout/sprout.js`).
* Difficulty, continue rules, recommendation timing: `CONFIG` at the top of `assets/engagement/engagement.js`.
* Picks: `assets/picks/picks.json`.

## Guardrails kept on purpose
* XP is only for real play: never for page loads, refreshes, affiliate clicks or purchases.
* No streak-loss shaming, no forced popups, no fake timers, no cash prizes.
* No claim of an ad when none plays: the continue button says "Get a small boost" until a rewarded-ad network is wired.
* No fingerprinting. The player ID is never sent to analytics.
* Amazon: exact disclosure, "(paid link)" labels, direct links, and no invented products, prices or ratings.

## Tests run before release
* 71 API checks (identity, 60 s validation, idempotency, concurrency, caps, weekly reset, leaderboard, rate limits, privacy controls, database permissions).
* 55 browser checks (first/return visits, persistence, cookies cleared, favourites, Picks compliance, mobile).
* 38 engagement checks (difficulty bounds, continue accept/decline/keyboard safety, ad wiring, Moonbloom, recommendations, streak, best scores, mobile).
* 27 fallback checks (rewards working with no server keys and with a failing database).

All ran against a local copy of the site and a local Postgres database, not against production.
