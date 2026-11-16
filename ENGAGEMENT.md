# Engagement layer (`window.PixelsproutEngage`)

`assets/engagement/engagement.js` (+ `engagement.css`). Vanilla JS, no
dependencies, and **all data stays in the browser** (localStorage). It is
loaded on every page by `pixelsprout-features.js`, so existing pages need no
changes. Games must treat it as optional: if it is missing, play normally at
multiplier 1.

It is separate from Sprout XP (`assets/sprout/`): XP is server-validated and
time-based. Nothing here awards XP, and continues never affect XP.

## What it does

| Feature | Where |
|---|---|
| Run history + personal bests per game | `saveScore`, `getPlayerState` |
| Gentle dynamic difficulty, **0.85x to 1.15x** | `getDifficultyMultiplier` |
| Optional "Continue this run?" at fail points | `gameOver` → `shouldOfferContinue` → offer UI → `showRewardedAd` |
| Site-wide daily play streak (local calendar) | `getStreak`; chip on game pages and the homepage |
| "Because you played X" | game pages after 150 s of visible play or on game over; homepage under Continue Playing |
| "Your best scores" | homepage and `/rewards/` (only when the player has a best) |
| Local analytics log | `stats()`, `log()`, `pixelsprout:engagement` DOM event |

Brand-new visitors see none of these panels until they have played.

## Including it in a new original game

The module is already on every page. In the game's own JS:

```js
var GAME_ID = 'my-game';                       // = data-slug on <body>
function engage() { return window.PixelsproutEngage || null; }

function startRun() {                          // every NEW run (not a continue)
  var e = engage();
  mult = e ? e.startRun(GAME_ID).multiplier : 1;
  speed = BASE_SPEED * mult;                   // apply to your tunables
}

function onFail() {
  var e = engage();
  if (e) e.gameOver(GAME_ID, score, {
    boostLabel: 'a 3-second shield',           // shown on the offer button
    onContinue: resumeRun,                     // resume the SAME run with an advantage
    onRestart: startRun                        // "No thanks, restart"
  });
}

function resumeRun() {
  engage() && engage().startRun(GAME_ID, { continued: true });
  shield = 3; state = 'play';
}

// A run that ends in success (level cleared) - records it, never offers a continue:
engage() && engage().completeRun(GAME_ID, score);
```

Requirements:
* `<body data-slug="..." data-title="..." data-genre="...">` (all game pages
  already have this). It labels history, best scores and recommendations.
* The canvas must sit inside `.stage .embed-frame-wrap` (already true). The offer
  is placed at the bottom of that box and works in fullscreen.
* Ignore the game's own restart key while the offer is focused. The module
  already stops key events inside the offer from reaching `window`.
* If the player restarts by any other route, `startRun()` closes the offer
  automatically. Call `PixelsproutEngage.dismissOffer()` when leaving the
  game-over screen some other way (e.g. back to a title screen).

## Which variables to make adjustable

Pick 2-3 tunables. `mult < 1` means help, `mult > 1` means challenge.

| Game type | Good tunables | Avoid |
|---|---|---|
| Runner (Dewlane) | scroll speed `* mult`, spawn interval `/ mult`, share of "hard" rows `* mult` | changing hitboxes or controls |
| Merge / puzzle (Moonbloom) | weight of blockers (pebbles) `* (1 + (mult-1)*2.5)`, weight of helpers (rain) `* (1 - (mult-1)*2)` | changing scoring rules or goals |
| Shooter / arcade | enemy speed, fire rate, helpful-drop chance | changing damage numbers mid-run |

Apply the multiplier **only at run start**, never during a run. Scores stay
comparable because the range is narrow (±15%).

### Example: runner (`games/dewlane.js`)
* `reset()` → `mult = startRun().multiplier`
* `speed = min(420, 180 + dist*0.04) * mult`
* `spawn = max(0.45, 1.15 - dist/8000) / mult`
* two-root rows: `kind < 0.62 * mult`
* `end()` → `gameOver(...)`. On continue: clear roots near the sprout and give a 3 s shield (drawn as a ring).

### Example: merge / puzzle (`games/moonbloom.p0`–`p3`)
* `freshRun()` → `mult = startRun().multiplier`
* `roll()` tilts the piece bag: fewer pebbles and more rain when `mult < 1`
* `finishTurn()` → `gameOver(...)` when the bed is full, `completeRun(...)` when an evening is cleared
* On continue: clear the top two rows and resume
* `onButton()` → `dismissOffer()`

### Minimal working example
`/examples/engagement-runner.html`: a ~90-line runner using the whole flow,
with a live dump of `getPlayerState()` and `stats()`. It is `noindex`.

## How the difficulty works

Constants are at the top of `engagement.js` (`CONFIG`).

1. Fewer than `MIN_RUNS_FOR_DDA` (3) runs → 1.0.
2. Looks at the last `RECENT_RUNS` (5):
   * **Struggling:** if at least `STRUGGLE_SHARE` (60%) ended in under `SHORT_RUN_SECONDS` (20 s), the target is `1 - 0.15 * share`.
   * **Skilled:** if the average is at least `SKILLED_RATIO` (75%) of the best and runs last at least `SKILLED_MIN_SECONDS` (45 s), the target is 1.06–1.15.
   * Otherwise the target is 1.0.
3. Moves halfway from the previous value toward the target (`SMOOTHING`), so it never jumps. Always clamped to `MIN_MULTIPLIER`–`MAX_MULTIPLIER`.

## How the continue offer works

`gameOver()` saves the score, then offers a continue only if **all** of these hold:
* the game supplied `onContinue`;
* the run lasted at least `MIN_RUN_SECONDS` (15 s) and scored at least `MIN_SCORE` (1);
* the score is at least `PROXIMITY` (70%) of the personal best before this run. On a first ever run (no best yet), it needs at least `FIRST_RUN_MIN_SECONDS` (40 s) instead;
* this run has not already used a continue (`MAX_CONTINUES_PER_RUN` = 1);
* fewer than `MAX_OFFERS_PER_SESSION` (4) offers on this page view;
* the player has not said "No thanks" `DECLINES_BEFORE_PAUSE` (3) times in a row.

The offer is a card at the bottom of the game stage. It is not modal: the
game's own Retry button, taps and keys still work. The two buttons are:
* **Continue**
  * No ad network wired (today): labelled "✨ Get a small boost" and resumes immediately. We never claim an ad was shown when none was.
  * Ad network wired: labelled "▶ Watch a short ad", calls `showRewardedAd()`, and resumes only if the ad reports `rewarded`.
* **No thanks, restart** → `onRestart()`.

### Wiring a real rewarded-ad network later
Define this before or after the module loads; nothing else changes:
```js
window.PixelsproutAds = {
  rewardedAvailable: function () { return true; },          // e.g. an ad is preloaded
  showRewarded: function () { return new Promise(function (resolve) {
    // network.show(..., onReward: () => resolve({ rewarded: true }), onSkip: () => resolve({ rewarded: false }))
  }); }
};
```
Check the ad network's and AdSense's policies for rewarded placements on web
before going live.

## Storage keys (all localStorage, this browser only)

| Key | Contents |
|---|---|
| `pse_games_v1` | `{ gameId: { best, runs:[{s,t,d}] (last 10), plays, mult, title, href, thumb, cat, lastPlayed } }` |
| `pse_streak_v1` | `{ last: 'YYYY-MM-DD', count, best }` |
| `pse_offers_v1` | `{ offered, accepted, declined }` |
| `pse_log_v1` | last 300 events: `session_start`, `session_end` (sec, visible sec, retries), `run_start` (multiplier), `game_over`, `run_complete`, `retry`, `continue_offer`, `continue_accept`, `continue_decline`, `recs_shown` |
| `pse_debug` | set to `'1'` to log every event to the console |

Games keep their own keys too (e.g. `dewlane-best`, `pixelsprout-moonbloom-v1`).

**Reset for testing:** `PixelsproutEngage.reset()` in the console (or delete
the `pse_*` keys). `PixelsproutEngage.CONFIG.MIN_RUN_SECONDS = 1` etc. lets
you trigger offers quickly in a single page view.

**Analytics:** `PixelsproutEngage.stats()` gives the average session length,
retries, offers, accept rate and average multiplier for this browser. Every
event is also dispatched as `document` `CustomEvent('pixelsprout:engagement')`.
Only three aggregate events are forwarded to the site's existing GA4:
`continue_offered`, `continue_accepted` and `game_retry` (params: `game_id`,
`difficulty_multiplier`, `retry_count`). No new third-party tracker was added.

## Notes
* "Because you played X" uses only this browser's history plus the genre lists
  in `assets/redesign/cat/`. It is not "players also played", because there is
  no cross-player data on the client and we don't claim there is.
* Streaks only ever show positively ("🔥 3-day streak" from 2 days up). Missing
  a day silently starts a new streak.
* Third-party embedded games get session tracking, the streak and
  time-based recommendations, but never difficulty changes or continue offers.
  We can't see inside them.
