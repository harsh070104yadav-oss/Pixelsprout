/*
 * Pixelsprout engagement layer (window.PixelsproutEngage)
 * -------------------------------------------------------
 * Small, dependency-free helpers that make runs feel fair and keep players
 * in flow:
 *   - per-game run history + personal bests       (getPlayerState, saveScore)
 *   - gentle dynamic difficulty, 0.85x - 1.15x     (getDifficultyMultiplier)
 *   - optional "continue this run" at fail points  (shouldOfferContinue, gameOver)
 *   - site-wide daily play streak                  (getStreak)
 *   - "Because you played X" recommendations and a "Your best scores" panel
 *   - local-only analytics log                     (stats, log)
 *
 * Everything is stored in THIS browser (localStorage). Nothing here sends
 * data to a server. A few aggregate events are forwarded to the site's
 * existing GA4 setup (no IDs, no scores tied to people) and every event is
 * also dispatched as a DOM CustomEvent "pixelsprout:engagement" so a future
 * analytics tool can listen without changing this file.
 *
 * Loaded on every page by pixelsprout-features.js. Games must treat it as
 * optional: if window.PixelsproutEngage is missing, play at multiplier 1.
 * See ENGAGEMENT.md for the integration contract.
 */
(function () {
  'use strict';
  if (window.PixelsproutEngage) return;

  // ===================================================================
  // TUNING - change these, not the code below.
  // ===================================================================
  var CONFIG = {
    // Dynamic difficulty ------------------------------------------------
    MIN_MULTIPLIER: 0.85,        // most help a struggling player gets
    MAX_MULTIPLIER: 1.15,        // most extra challenge for a skilled player
    MIN_RUNS_FOR_DDA: 3,         // play at 1.0 until we have this many runs
    RECENT_RUNS: 5,              // runs considered "recent"
    SHORT_RUN_SECONDS: 20,       // a run shorter than this counts as a quick fail
    STRUGGLE_SHARE: 0.6,         // >= 60% quick fails in recent runs -> help
    SKILLED_RATIO: 0.75,         // recent avg >= 75% of best ...
    SKILLED_MIN_SECONDS: 45,     // ... and runs last this long -> ramp up
    SMOOTHING: 0.5,              // 0 = jump straight to target, 1 = never change

    // Continue offers ---------------------------------------------------
    PROXIMITY: 0.7,              // offer when score >= 70% of personal best
    MIN_RUN_SECONDS: 15,         // never offer after an instant fail
    MIN_SCORE: 1,                // ...or a zero score
    MAX_CONTINUES_PER_RUN: 1,    // one continue per run
    MAX_OFFERS_PER_SESSION: 4,   // per page view
    DECLINES_BEFORE_PAUSE: 3,    // stop offering in this session after N "no thanks" in a row
    FIRST_RUN_OFFER: true,       // allow an offer on a player's very first run with no best yet (long runs only)
    FIRST_RUN_MIN_SECONDS: 40,

    // Site-wide retention UI ---------------------------------------------
    RECS_AFTER_SECONDS: 150,     // show "Because you played" after this much visible time
    RECS_COUNT: 6,
    BEST_SCORES_SHOWN: 6,
    HISTORY_PER_GAME: 10,        // runs remembered per game
    LOG_MAX: 300                 // local analytics entries kept
  };

  var K = {                      // localStorage keys (see ENGAGEMENT.md)
    games: 'pse_games_v1',       // { gameId: { best, runs:[{s,t,d}], plays, mult, title, href, thumb, cat } }
    streak: 'pse_streak_v1',     // { last:'YYYY-MM-DD', count, best }
    log: 'pse_log_v1',           // [ { t, e, g, ... } ]
    offers: 'pse_offers_v1',     // { offered, accepted, declined }
    debug: 'pse_debug'           // '1' -> console logging
  };
  var RECENT_KEY = 'pixelsprout_recent_v2'; // existing site key (recently played)

  // ------------------------------------------------------------ storage --
  function read(key, fb) { try { var v = localStorage.getItem(key); return v ? JSON.parse(v) : fb; } catch (e) { return fb; } }
  function write(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch (e) { /* private mode / full */ } }
  function clamp(x, a, b) { return Math.max(a, Math.min(b, x)); }
  function today() { var d = new Date(); return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); }
  function dayDiff(a, b) { return Math.round((Date.parse(b + 'T00:00:00') - Date.parse(a + 'T00:00:00')) / 86400000); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function $(s, r) { return (r || document).querySelector(s); }

  function games() { var g = read(K.games, {}); return g && typeof g === 'object' ? g : {}; }
  function gameRec(all, id) {
    var g = all[id];
    if (!g) g = all[id] = { best: 0, runs: [], plays: 0, mult: 1 };
    return g;
  }

  // ---------------------------------------------------------- analytics --
  var GA_FORWARD = { continue_offer: 'continue_offered', continue_accept: 'continue_accepted', retry: 'game_retry' };
  function log(type, data) {
    var entry = { t: Date.now(), e: type };
    for (var k in data) if (Object.prototype.hasOwnProperty.call(data, k)) entry[k] = data[k];
    var list = read(K.log, []);
    list.push(entry);
    if (list.length > CONFIG.LOG_MAX) list = list.slice(-CONFIG.LOG_MAX);
    write(K.log, list);
    try { document.dispatchEvent(new CustomEvent('pixelsprout:engagement', { detail: entry })); } catch (e) { /* old browser */ }
    if (localStorageGet(K.debug) === '1') console.info('[engage]', type, entry);
    if (GA_FORWARD[type] && typeof window.gtag === 'function') {
      window.gtag('event', GA_FORWARD[type], { game_id: entry.g || '', difficulty_multiplier: entry.m, retry_count: entry.retry });
    }
    return entry;
  }
  function localStorageGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }

  // -------------------------------------------------------------- streak --
  // Site-wide daily streak in the player's LOCAL calendar. Missing a day just
  // starts a new streak; we never show "you lost your streak".
  function touchStreak() {
    var s = read(K.streak, { last: '', count: 0, best: 0 });
    var d = today();
    if (s.last === d) return s;
    var gap = s.last ? dayDiff(s.last, d) : 99;
    s.count = gap === 1 ? s.count + 1 : 1;
    s.last = d;
    s.best = Math.max(s.best || 0, s.count);
    write(K.streak, s);
    return s;
  }
  function getStreak() {
    var s = read(K.streak, { last: '', count: 0, best: 0 });
    if (!s.last) return { count: 0, best: 0, active: false };
    var gap = dayDiff(s.last, today());
    return { count: gap <= 1 ? s.count : 0, best: s.best || 0, active: gap <= 1, playedToday: gap === 0 };
  }

  // ------------------------------------------------------- player state --
  function getPlayerState(gameId) {
    var g = games()[gameId] || { best: 0, runs: [], plays: 0, mult: 1 };
    var recent = g.runs.slice(-CONFIG.RECENT_RUNS);
    var scores = recent.map(function (r) { return r.s; });
    var avg = scores.length ? scores.reduce(function (a, b) { return a + b; }, 0) / scores.length : 0;
    var shortFails = recent.filter(function (r) { return r.t < CONFIG.SHORT_RUN_SECONDS; }).length;
    // streak of improving runs (each run beating the previous one)
    var improving = 0;
    for (var i = g.runs.length - 1; i > 0 && g.runs[i].s > g.runs[i - 1].s; i--) improving++;
    return {
      gameId: gameId,
      plays: g.plays || 0,
      bestScore: g.best || 0,
      avgScore: Math.round(avg),
      failureRate: recent.length ? shortFails / recent.length : 0,   // share of recent runs that were quick fails
      streak: improving,                                            // consecutive improving runs
      lastRunLengths: recent.map(function (r) { return r.t; }),     // seconds, oldest -> newest
      lastScores: scores,
      multiplier: g.mult || 1,
      dailyStreak: getStreak().count
    };
  }

  // Target multiplier from recent form, smoothed so it never jumps.
  function getDifficultyMultiplier(gameId) {
    var all = games();
    var g = all[gameId];
    if (!g || g.runs.length < CONFIG.MIN_RUNS_FOR_DDA) return 1;
    var st = getPlayerState(gameId);
    var target = 1;
    var avgLen = st.lastRunLengths.reduce(function (a, b) { return a + b; }, 0) / Math.max(1, st.lastRunLengths.length);
    if (st.failureRate >= CONFIG.STRUGGLE_SHARE) {
      // Struggling: help in proportion to how often runs end quickly.
      target = 1 - (1 - CONFIG.MIN_MULTIPLIER) * st.failureRate;
    } else if (st.bestScore > 0 && st.avgScore / st.bestScore >= CONFIG.SKILLED_RATIO && avgLen >= CONFIG.SKILLED_MIN_SECONDS) {
      // Consistently near their best with long runs: add challenge.
      var k = clamp((st.avgScore / st.bestScore - CONFIG.SKILLED_RATIO) / (1 - CONFIG.SKILLED_RATIO), 0, 1);
      target = 1 + (CONFIG.MAX_MULTIPLIER - 1) * (0.4 + 0.6 * k);
    }
    var prev = g.mult || 1;
    var m = prev * CONFIG.SMOOTHING + target * (1 - CONFIG.SMOOTHING);
    if (Math.abs(m - target) < 0.01) m = target;   // settle instead of creeping forever
    m = clamp(m, CONFIG.MIN_MULTIPLIER, CONFIG.MAX_MULTIPLIER);
    return Math.round(m * 100) / 100;
  }

  // Records a finished run. Returns { best, newBest, previousBest }.
  function saveScore(gameId, score, runTime) {
    score = Math.max(0, Math.floor(Number(score) || 0));
    runTime = Math.max(0, Math.round(Number(runTime) || 0));
    var all = games();
    var g = gameRec(all, gameId);
    var prev = g.best || 0;
    g.runs.push({ s: score, t: runTime, d: Date.now() });
    if (g.runs.length > CONFIG.HISTORY_PER_GAME) g.runs = g.runs.slice(-CONFIG.HISTORY_PER_GAME);
    g.best = Math.max(prev, score);
    g.lastPlayed = Date.now();
    var meta = pageGame();
    if (meta && meta.slug === gameId) { g.title = meta.title; g.href = meta.href; g.thumb = meta.thumbnail; g.cat = meta.category; }
    write(K.games, all);
    return { best: g.best, newBest: score > prev && prev > 0, previousBest: prev };
  }

  // ------------------------------------------------------- continue logic --
  var session = { offers: 0, declinesInRow: 0, runContinues: {}, runStart: {}, retries: {}, current: null };

  // previousBest: the personal best BEFORE this run. If omitted, the stored
  // best is used, so call this before saveScore() when using it directly.
  function shouldOfferContinue(gameId, score, runTime, previousBest) {
    if (runTime == null) runTime = session.runStart[gameId] ? (Date.now() - session.runStart[gameId]) / 1000 : 0;
    if (session.offers >= CONFIG.MAX_OFFERS_PER_SESSION) return false;
    if (session.declinesInRow >= CONFIG.DECLINES_BEFORE_PAUSE) return false;
    if ((session.runContinues[gameId] || 0) >= CONFIG.MAX_CONTINUES_PER_RUN) return false;
    if (score < CONFIG.MIN_SCORE || runTime < CONFIG.MIN_RUN_SECONDS) return false;
    var ref = previousBest != null ? previousBest : getPlayerState(gameId).bestScore;
    if (!ref) return CONFIG.FIRST_RUN_OFFER && runTime >= CONFIG.FIRST_RUN_MIN_SECONDS;
    return score >= ref * CONFIG.PROXIMITY;     // near (or beyond) a personal best
  }

  // Rewarded ad placeholder. Wire a real network by defining
  // window.PixelsproutAds = { rewardedAvailable(): bool, showRewarded(): Promise<{rewarded:boolean}> }.
  // Without it we never pretend to show an ad: the offer is labelled as a
  // free boost and resolves immediately.
  function adsAvailable() {
    var a = window.PixelsproutAds;
    return !!(a && typeof a.showRewarded === 'function' && (typeof a.rewardedAvailable !== 'function' || a.rewardedAvailable()));
  }
  function showRewardedAd() {
    if (adsAvailable()) {
      return Promise.resolve(window.PixelsproutAds.showRewarded()).then(function (r) {
        return { rewarded: !(r && r.rewarded === false), viaAd: true };
      }, function () { return { rewarded: false, viaAd: true }; });
    }
    return Promise.resolve({ rewarded: true, viaAd: false });
  }

  // ------------------------------------------------------------ run hooks --
  // Games call startRun() when a run begins and gameOver() when it ends.
  function startRun(gameId, opts) {
    dismissOffer();
    var isContinue = opts && opts.continued;
    if (!isContinue) {
      var all = games();
      var g = gameRec(all, gameId);
      var m = getDifficultyMultiplier(gameId);
      g.mult = m;
      g.plays = (g.plays || 0) + 1;
      write(K.games, all);
      if (session.runStart[gameId]) {
        session.retries[gameId] = (session.retries[gameId] || 0) + 1;
        log('retry', { g: gameId, retry: session.retries[gameId] });
      }
      session.runStart[gameId] = Date.now();
      session.runContinues[gameId] = 0;
      log('run_start', { g: gameId, m: m });
      return { multiplier: m, state: getPlayerState(gameId) };
    }
    return { multiplier: (games()[gameId] || {}).mult || 1, state: getPlayerState(gameId) };
  }

  /*
   * gameOver(gameId, score, opts)
   *   opts.onContinue(boost) - required to allow continues; resume the run with an advantage
   *   opts.onRestart()       - optional; called by "No thanks, restart"
   *   opts.boostLabel        - short text for the advantage, e.g. "2 s shield"
   *   opts.mount             - element to place the offer in (default: the game stage)
   * Returns { offered, best, newBest }.
   */
  function gameOver(gameId, score, opts) {
    opts = opts || {};
    var started = session.runStart[gameId] || Date.now();
    var runTime = (Date.now() - started) / 1000;
    var res = saveScore(gameId, score, runTime);
    log('game_over', { g: gameId, s: Math.floor(score), rt: Math.round(runTime), m: (games()[gameId] || {}).mult || 1, retry: session.retries[gameId] || 0 });
    var offered = false;
    if (typeof opts.onContinue === 'function' && shouldOfferContinue(gameId, Math.floor(score), runTime, res.previousBest)) {
      offered = true;
      offerContinue(gameId, Math.floor(score), res, opts);
    }
    showRecommendations('gameover');
    renderGameBest(gameId);
    return { offered: offered, best: res.best, newBest: res.newBest };
  }

  // A run that ended in success (level cleared): record it, never offer a continue.
  function completeRun(gameId, score) {
    var started = session.runStart[gameId] || Date.now();
    var runTime = (Date.now() - started) / 1000;
    var res = saveScore(gameId, score, runTime);
    log('run_complete', { g: gameId, s: Math.floor(score), rt: Math.round(runTime), m: (games()[gameId] || {}).mult || 1 });
    renderGameBest(gameId);
    return res;
  }

  // -------------------------------------------------------- continue UI --
  var offerEl = null;
  function dismissOffer() { if (offerEl) { offerEl.remove(); offerEl = null; } }

  function offerContinue(gameId, score, res, opts) {
    ensureCss();
    dismissOffer();
    session.offers++;
    var counts = read(K.offers, { offered: 0, accepted: 0, declined: 0 });
    counts.offered++;
    write(K.offers, counts);
    var withAd = adsAvailable();
    log('continue_offer', { g: gameId, s: score, viaAd: withAd });

    var best = res.previousBest;
    var headline = best > 0 && score > best ? 'New personal best!' : best && score >= best * 0.9 ? 'So close to your best!' : 'Great run!';
    var gap = !best ? 'your first big run' : best > score ? (best - score) + ' from your best' : score > best ? 'past your old best of ' + best : 'tied with your best';
    var boost = opts.boostLabel || 'a small boost';

    var host = opts.mount || $('.stage .embed-frame-wrap') || $('.stage') || document.body;
    var el = document.createElement('div');
    el.className = 'pse-offer';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'false');
    el.setAttribute('aria-labelledby', 'pse-offer-title');
    el.innerHTML =
      '<div class="pse-offer__card">' +
      '<p class="pse-offer__eyebrow">' + esc(headline) + '</p>' +
      '<p class="pse-offer__title" id="pse-offer-title">Continue this run?</p>' +
      '<p class="pse-offer__meta"><b>' + esc(score) + '</b> · ' + esc(gap) + '</p>' +
      '<div class="pse-offer__actions">' +
      '<button type="button" class="pse-offer__go" data-pse="go">' + (withAd ? '▶ Watch a short ad' : '✨ Get a small boost') + '<small>Resume with ' + esc(boost) + '</small></button>' +
      '<button type="button" class="pse-offer__no" data-pse="no">No thanks, restart</button>' +
      '</div>' +
      (withAd ? '<p class="pse-offer__note">Optional. The ad plays only if you choose it.</p>' : '') +
      '</div>';
    // Keys pressed inside the offer must not reach the game's window
    // listeners (Enter/Space would otherwise also restart the game).
    el.addEventListener('keydown', function (e) {
      e.stopPropagation();
      if (e.key === 'Escape') { decline(false); }
    });
    el.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
    host.appendChild(el);
    offerEl = el;
    requestAnimationFrame(function () { el.classList.add('is-in'); });

    function decline(restart) {
      var c = read(K.offers, { offered: 0, accepted: 0, declined: 0 });
      c.declined++;
      write(K.offers, c);
      session.declinesInRow++;
      log('continue_decline', { g: gameId });
      dismissOffer();
      if (restart && typeof opts.onRestart === 'function') opts.onRestart();
    }
    $('[data-pse="no"]', el).addEventListener('click', function () { decline(true); });
    $('[data-pse="go"]', el).addEventListener('click', function () {
      var btn = this;
      btn.disabled = true;
      showRewardedAd().then(function (r) {
        if (!r.rewarded) { btn.disabled = false; return; }
        var c = read(K.offers, { offered: 0, accepted: 0, declined: 0 });
        c.accepted++;
        write(K.offers, c);
        session.declinesInRow = 0;
        session.runContinues[gameId] = (session.runContinues[gameId] || 0) + 1;
        // The continued run counts from now for history purposes.
        session.runStart[gameId] = Date.now();
        log('continue_accept', { g: gameId, s: score, viaAd: r.viaAd });
        dismissOffer();
        try { opts.onContinue({ viaAd: r.viaAd }); } catch (e) { console.error(e); }
      });
    });
  }

  // ------------------------------------------------- page-level tracking --
  function pageGame() {
    var b = document.body;
    if (!b) return null;
    var slug = b.getAttribute('data-slug') || b.getAttribute('data-tool-slug');
    if (!slug) return null;
    return {
      slug: slug,
      title: b.getAttribute('data-title') || (($('.game-bar__title, .game-title') || {}).textContent || '').trim() || document.title,
      category: b.getAttribute('data-genre') || '',
      thumbnail: b.getAttribute('data-thumbnail') || '',
      href: location.pathname
    };
  }

  // Records the start of a page session for this game and its visible time.
  var visibleSeconds = 0;
  function trackSession(gameId) {
    var id = gameId || (pageGame() || {}).slug;
    if (!id || session.current) return session.current;
    session.current = { gameId: id, start: Date.now() };
    touchStreak();
    log('session_start', { g: id });
    var timer = setInterval(function () {
      if (document.visibilityState === 'visible') visibleSeconds++;
      if (visibleSeconds === CONFIG.RECS_AFTER_SECONDS) showRecommendations('time');
    }, 1000);
    var ended = false;
    function end() {
      if (ended) return;
      ended = true;
      clearInterval(timer);
      log('session_end', { g: id, sec: Math.round((Date.now() - session.current.start) / 1000), vis: visibleSeconds, retry: session.retries[id] || 0 });
    }
    window.addEventListener('pagehide', end);
    return session.current;
  }

  // -------------------------------------------------- recommendations --
  function slugify(s) { return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''); }
  function genreList(genre) {
    if (!genre) return Promise.resolve([]);
    return fetch('/assets/redesign/cat/' + slugify(genre) + '.json').then(function (r) { return r.ok ? r.json() : []; }).catch(function () { return []; });
  }
  function recentLocal() { var r = read(RECENT_KEY, []); return Array.isArray(r) ? r : []; }

  // "Because you played X": same genre first, then the player's favourite
  // other genre (from local history), skipping games they already played.
  function recommendFor(game) {
    var recent = recentLocal();
    var seen = {};
    recent.forEach(function (g) { if (g && g.slug) seen[g.slug] = 1; });
    seen[game.slug] = 1;
    var counts = {};
    recent.forEach(function (g) { if (g && g.category && g.category !== game.category && g.category !== 'Pixelsprout original') counts[g.category] = (counts[g.category] || 0) + 1; });
    var other = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; })[0];
    return Promise.all([genreList(game.category), genreList(other)]).then(function (lists) {
      var out = [];
      var take = function (list, n) {
        list.forEach(function (g) {
          if (out.length >= n || !g || !g.s || seen[g.s]) return;
          seen[g.s] = 1;
          out.push({ slug: g.s, title: g.t, category: g.c, thumbnail: g.i, original: g.m === 1 });
        });
      };
      take(lists[0], other ? CONFIG.RECS_COUNT - 2 : CONFIG.RECS_COUNT);
      take(lists[1], CONFIG.RECS_COUNT);
      take(lists[0], CONFIG.RECS_COUNT);
      return out;
    });
  }

  function cardHTML(g) {
    return '<a class="card" href="/games/' + esc(g.slug) + '.html" data-name="' + esc((g.title || '').toLowerCase()) + '">' +
      '<span class="card__media"><img src="' + esc(g.thumbnail || '/assets/logo-icon.png') + '" alt="' + esc(g.title) + '" width="320" height="320" loading="lazy" decoding="async">' +
      (g.original ? '<span class="card__badge">Original</span>' : '') + '</span>' +
      '<span class="card__title">' + esc(g.title) + '</span>' + (g.category ? '<span class="card__meta">' + esc(g.category) + '</span>' : '') + '</a>';
  }

  function shelfHTML(id, title, inner, extraHead) {
    return '<section class="shelf pse-shelf" id="' + id + '" aria-labelledby="' + id + '-title">' +
      '<div class="shelf__head"><h2 class="shelf__title" id="' + id + '-title">' + title + '</h2><span class="shelf__spacer"></span>' + (extraHead || '') + '</div>' +
      '<div class="shelf__track pse-track">' + inner + '</div></section>';
  }

  var recsShown = false;
  function showRecommendations(reason) {
    var game = pageGame();
    if (recsShown || !game || !game.category) return;
    var anchor = $('.game-bar') || $('.game-about');
    if (!anchor) return;
    recsShown = true;
    recommendFor(game).then(function (list) {
      if (list.length < 3) return;
      ensureCss();
      var wrap = document.createElement('div');
      wrap.innerHTML = shelfHTML('pse-because', 'Because you played <em>' + esc(game.title) + '</em>', list.map(cardHTML).join(''));
      var sec = wrap.firstChild;
      sec.classList.add('pse-reveal');
      anchor.parentNode.insertBefore(sec, anchor.nextSibling);
      requestAnimationFrame(function () { sec.classList.add('is-in'); });
      log('recs_shown', { g: game.slug, why: reason, n: list.length });
    });
  }

  // ------------------------------------------------------ best scores --
  function bestScores() {
    var all = games();
    return Object.keys(all).filter(function (id) { return all[id].best > 0 && all[id].title; })
      .sort(function (a, b) { return (all[b].lastPlayed || 0) - (all[a].lastPlayed || 0); })
      .slice(0, CONFIG.BEST_SCORES_SHOWN)
      .map(function (id) { var g = all[id]; return { slug: id, title: g.title, href: g.href || '/games/' + id + '.html', thumbnail: g.thumb, best: g.best, plays: g.plays || 0 }; });
  }

  function bestPanelHTML(list) {
    return '<section class="pse-best" id="pse-best" aria-labelledby="pse-best-title">' +
      '<div class="pse-best__head"><h2 class="pse-best__title" id="pse-best-title"><span aria-hidden="true">🏆</span> Your best scores</h2>' + streakChipHTML() + '</div>' +
      '<ol class="pse-best__list">' + list.map(function (g) {
        return '<li><a href="' + esc(g.href) + '"><img src="' + esc(g.thumbnail || '/assets/logo-icon.png') + '" alt="" width="40" height="40" loading="lazy" decoding="async">' +
          '<span class="pse-best__name">' + esc(g.title) + '<small>' + esc(g.plays) + ' run' + (g.plays === 1 ? '' : 's') + '</small></span>' +
          '<span class="pse-best__score">' + esc(g.best.toLocaleString('en-US')) + '</span><span class="pse-best__cta" aria-hidden="true">Beat it →</span></a></li>';
      }).join('') + '</ol></section>';
  }

  function streakChipHTML() {
    var s = getStreak();
    if (!s.active || s.count < 2) return '';
    return '<span class="pse-streak" title="Days in a row you have played on Pixelsprout"><span aria-hidden="true">🔥</span> ' + esc(s.count) + '-day streak</span>';
  }

  // Game page: personal best + streak chips beside the title.
  function renderGameBest(gameId) {
    var info = $('.game-bar__info');
    if (!info) return;
    ensureCss();
    var g = games()[gameId];
    var chip = $('.pse-chip-best', info);
    if (g && g.best > 0) {
      if (!chip) { chip = document.createElement('span'); chip.className = 'chip pse-chip-best'; info.appendChild(chip); }
      chip.innerHTML = '<span aria-hidden="true">🏆</span> Best ' + esc(g.best.toLocaleString('en-US'));
    }
    if (!$('.pse-streak', info)) {
      var s = streakChipHTML();
      if (s) info.insertAdjacentHTML('beforeend', s);
    }
  }

  // Homepage: "Because you played <last game>", best scores, streak.
  function initHome() {
    if (document.body.getAttribute('data-page') !== 'home') return;
    var cont = $('#continue-playing');
    var recent = recentLocal();
    var last = recent[0];
    var anchor = cont || $('#popular-now');
    if (!anchor) return;
    var list = bestScores();
    if (list.length) {
      ensureCss();
      anchor.insertAdjacentHTML('afterend', bestPanelHTML(list));
    }
    if (last && last.slug && last.category && last.category !== 'Pixelsprout original') {
      recommendFor(last).then(function (recs) {
        if (recs.length < 3) return;
        ensureCss();
        var after = $('#pse-best') || anchor;
        after.insertAdjacentHTML('afterend', shelfHTML('pse-because', 'Because you played <em>' + esc(last.title) + '</em>', recs.map(cardHTML).join(''),
          list.length ? '' : streakChipHTML()));
      });
    }
  }

  // Rewards page: best scores next to Sprout XP.
  function initRewards() {
    var page = $('[data-sprout-page="rewards"]');
    if (!page) return;
    var list = bestScores();
    if (!list.length) return;
    ensureCss();
    var grid = $('.sp-grid', page);
    if (grid) grid.insertAdjacentHTML('afterend', bestPanelHTML(list));
  }

  // -------------------------------------------------------------- misc --
  function ensureCss() {
    if ($('link[href^="/assets/engagement/engagement.css"]')) return;
    var l = document.createElement('link');
    l.rel = 'stylesheet';
    l.href = '/assets/engagement/engagement.css?v=20261116b';
    document.head.appendChild(l);
  }

  // Aggregate numbers for tuning (run PixelsproutEngage.stats() in the console).
  function stats() {
    var entries = read(K.log, []);
    var o = read(K.offers, { offered: 0, accepted: 0, declined: 0 });
    var sessions = entries.filter(function (e) { return e.e === 'session_end'; });
    var mults = entries.filter(function (e) { return e.e === 'run_start'; }).map(function (e) { return e.m; });
    return {
      sessions: sessions.length,
      avgSessionSeconds: sessions.length ? Math.round(sessions.reduce(function (a, e) { return a + e.sec; }, 0) / sessions.length) : 0,
      retries: entries.filter(function (e) { return e.e === 'retry'; }).length,
      continueOffers: o.offered,
      continueAccepted: o.accepted,
      continueAcceptRate: o.offered ? Math.round(o.accepted / o.offered * 100) / 100 : 0,
      avgMultiplier: mults.length ? Math.round(mults.reduce(function (a, b) { return a + b; }, 0) / mults.length * 100) / 100 : 1,
      streak: getStreak()
    };
  }

  function reset() { Object.keys(K).forEach(function (k) { try { localStorage.removeItem(K[k]); } catch (e) { /* ignore */ } }); }

  window.PixelsproutEngage = {
    CONFIG: CONFIG,
    KEYS: K,
    trackSession: trackSession,
    getPlayerState: getPlayerState,
    saveScore: saveScore,
    getDifficultyMultiplier: getDifficultyMultiplier,
    shouldOfferContinue: shouldOfferContinue,
    getStreak: getStreak,
    startRun: startRun,
    gameOver: gameOver,
    completeRun: completeRun,
    dismissOffer: dismissOffer,
    showRewardedAd: showRewardedAd,
    showRecommendations: showRecommendations,
    bestScores: bestScores,
    stats: stats,
    log: log,
    reset: reset
  };
  window.showRewardedAd = window.showRewardedAd || showRewardedAd;

  function init() {
    var g = pageGame();
    if (g && document.body.getAttribute('data-page') !== 'rewards') { trackSession(g.slug); renderGameBest(g.slug); }
    initHome();
    initRewards();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
