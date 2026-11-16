/*
 * Pixelsprout player layer: anonymous Sprout XP, quests, weekly league,
 * favourites/recent sync, game-session measurement and Pixelsprout Picks.
 * Loaded (deferred) by pixelsprout-features.js on every page.
 *
 * Privacy: the anonymous player ID lives in an HttpOnly cookie that this
 * script cannot read. Nothing here sends the ID, display name or any personal
 * data to Google Analytics; GA4 events carry only game/category/XP context.
 *
 * XP: this script never decides or submits XP. It reports "game page opened"
 * and "60 seconds of active play" to /api/sprout, which validates both
 * against the server clock and awards XP through an idempotent ledger.
 */
(function () {
  'use strict';
  if (window.__pixelsproutSprout) return;
  window.__pixelsproutSprout = true;

  var API = '/api/sprout';
  var RECENT_KEY = 'pixelsprout_recent_v2';
  var FAVORITES_KEY = 'pixelsprout_favorites_v2';
  var IMPORTED_KEY = 'pixelsprout_sprout_imported_v1';
  var SEEN_COMEBACK_KEY = 'pixelsprout_comeback_seen';
  var STORE_KEY = 'pixelsprout_picks_store';
  var CSS_HREF = '/assets/sprout/sprout.css?v=20261116a';
  var TOOLS = {
    'cold-read': { slug: 'cold-read', title: 'Cold Read', category: 'Pixelsprout original', thumbnail: '/assets/originals/cold-read/cover.webp', href: '/cold-read/' },
    'gift-file': { slug: 'gift-file', title: 'Guess It Box', category: 'Pixelsprout original', thumbnail: '/assets/originals/guess-it-box/cover.webp', href: '/gift-file/' }
  };

  // ------------------------------------------------------------ helpers --

  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function load(key, fallback) { try { var v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch (e) { return fallback; } }
  function save(key, val) { try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) { /* storage unavailable */ } }
  function reducedMotion() { return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches; }

  function ensureCss() {
    if ($('link[href^="/assets/sprout/sprout.css"]')) return;
    var l = document.createElement('link');
    l.rel = 'stylesheet';
    l.href = CSS_HREF;
    document.head.appendChild(l);
  }

  // GA4: queue until the (delayed) gtag loader has run. Never include IDs.
  var gaQueue = [];
  var gaTimer = null;
  function ga(name, params) {
    gaQueue.push([name, params || {}]);
    flushGa();
  }
  function flushGa() {
    if (typeof window.gtag === 'function') {
      while (gaQueue.length) { var e = gaQueue.shift(); window.gtag('event', e[0], e[1]); }
      return;
    }
    if (!gaTimer) {
      var tries = 0;
      gaTimer = setInterval(function () {
        tries++;
        if (typeof window.gtag === 'function' || tries > 30) {
          clearInterval(gaTimer); gaTimer = null;
          if (typeof window.gtag === 'function') flushGa(); else gaQueue.length = 0;
        }
      }, 1000);
    }
  }

  function api(method, body, query) {
    var opts = { method: method, credentials: 'same-origin', headers: {} };
    if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    return fetch(API + (query || ''), opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { j.__status = r.status; return j; });
    });
  }

  // ------------------------------------------------- game metadata lookup --

  var catalogP = null;
  function catalog() {
    if (!catalogP) {
      catalogP = fetch('/games-index.json').then(function (r) { return r.ok ? r.json() : []; }).then(function (list) {
        var m = {};
        list.forEach(function (g) { if (g && g.slug && !m[g.slug]) m[g.slug] = g; });
        return m;
      }).catch(function () { catalogP = null; return {}; });
    }
    return catalogP;
  }

  // Resolve slugs to {slug,title,category,thumbnail} using local copies first.
  function resolveGames(slugs) {
    var known = {};
    load(RECENT_KEY, []).concat(load(FAVORITES_KEY, [])).forEach(function (g) { if (g && g.slug && !known[g.slug]) known[g.slug] = g; });
    Object.keys(TOOLS).forEach(function (k) { known[k] = TOOLS[k]; });
    var missing = slugs.filter(function (s) { return !known[s]; });
    var p = missing.length ? catalog() : Promise.resolve({});
    return p.then(function (cat) {
      return slugs.map(function (s) {
        var g = known[s] || cat[s];
        return g ? { slug: s, title: g.title, category: g.category || '', thumbnail: g.thumbnail || '', href: g.href } : null;
      }).filter(Boolean);
    });
  }

  function hrefFor(g) { return g.href || (TOOLS[g.slug] ? TOOLS[g.slug].href : '/games/' + g.slug + '.html'); }
  function cardHTML(g) {
    var thumb = g.thumbnail && String(g.thumbnail).trim() ? g.thumbnail : '/assets/logo-icon.png';
    if (thumb.indexOf('../') === 0) thumb = thumb.slice(2);
    return '<a class="card" href="' + esc(hrefFor(g)) + '" data-name="' + esc((g.title || '').toLowerCase()) + '">' +
      '<span class="card__media"><img src="' + esc(thumb) + '" alt="' + esc(g.title) + '" width="320" height="320" loading="lazy" decoding="async"></span>' +
      '<span class="card__title">' + esc(g.title) + '</span>' +
      (g.category ? '<span class="card__meta">' + esc(g.category) + '</span>' : '') + '</a>';
  }

  // ------------------------------------------------------------- toasts --

  var stack;
  function notify(html, opts) {
    ensureCss();
    if (!stack) {
      stack = document.createElement('div');
      stack.className = 'sp-toasts';
      stack.setAttribute('role', 'status');
      stack.setAttribute('aria-live', 'polite');
      document.body.appendChild(stack);
    }
    var el = document.createElement('div');
    el.className = 'sp-toast' + (opts && opts.kind ? ' sp-toast--' + opts.kind : '');
    el.innerHTML = html;
    stack.appendChild(el);
    requestAnimationFrame(function () { el.classList.add('is-in'); });
    setTimeout(function () {
      el.classList.remove('is-in');
      setTimeout(function () { el.remove(); }, 400);
    }, (opts && opts.ms) || 6000);
  }

  function comebackHTML(xp) {
    return '<p class="sp-toast__title"><span aria-hidden="true">🌱</span> Welcome back to Pixelsprout!</p>' +
      '<p class="sp-toast__xp">+' + esc(xp) + ' Sprout XP</p><p class="sp-toast__sub">Keep exploring and growing!</p>';
  }

  function announceAwards(res, ctx) {
    (res.awards || []).forEach(function (a) {
      var p = { xp_amount: a.xp, xp_reason: a.reason };
      if (ctx) { p.game_title = ctx.title; p.game_category = ctx.category; }
      if (a.xp > 0) ga('xp_awarded', p);
      if (a.reason === 'return_day') {
        ga('return_qualifying_visit', { game_category: ctx ? ctx.category : '' });
        save(SEEN_COMEBACK_KEY, res.progress ? res.progress.day : '');
        if (a.xp > 0) notify(comebackHTML(a.xp), { kind: 'comeback', ms: 7000 });
      } else if (a.reason === 'weekly_return') {
        ga('weekly_return_bonus_awarded', { xp_amount: a.xp });
        if (a.xp > 0) notify('<p class="sp-toast__title"><span aria-hidden="true">🌿</span> Weekly return bonus</p><p class="sp-toast__xp">+' + esc(a.xp) + ' Sprout XP</p><p class="sp-toast__sub">3 active days this week. Nice!</p>', { kind: 'quest' });
      } else if (a.reason === 'daily_quest' || a.reason === 'weekly_quest') {
        var id = (a.key || '').split(':')[1] || '';
        ga('quest_completed', { quest_id: id, quest_period: a.reason === 'daily_quest' ? 'daily' : 'weekly', xp_amount: a.xp });
        notify('<p class="sp-toast__title"><span class="sp-check" aria-hidden="true">✓</span> Quest complete</p><p class="sp-toast__sub">' + esc(a.label) + '</p>' +
          (a.xp > 0 ? '<p class="sp-toast__xp">+' + esc(a.xp) + ' Sprout XP</p>' : '<p class="sp-toast__sub">Daily XP limit reached</p>'), { kind: 'quest' });
      } else if (a.reason === 'first_session' && a.xp > 0) {
        notify('<p class="sp-toast__title"><span aria-hidden="true">🌱</span> Your first Sprout XP!</p><p class="sp-toast__xp">+' + esc(a.xp) + ' Sprout XP</p><p class="sp-toast__sub"><a href="/rewards/">See quests &amp; league</a></p>', { kind: 'quest' });
      }
    });
    if (res.level_up) {
      ga('badge_unlocked', { badge_name: res.level_up.to, badge_level: res.level_up.level });
      notify('<p class="sp-toast__title"><span aria-hidden="true">🏅</span> Level up!</p><p class="sp-toast__sub">You are now a <strong>' + esc(res.level_up.to) + '</strong>.</p>', { kind: 'level', ms: 7000 });
    }
  }

  // -------------------------------------------------- favourites + recent --

  // pixelsprout-features.js dispatches this when the heart is toggled.
  document.addEventListener('pixelsprout:favorite', function (e) {
    var d = e.detail || {};
    if (!d.slug) return;
    api('POST', { action: 'favorite', slug: d.slug, on: !!d.on }).catch(function () {});
  });

  // Server lists are the source of truth once they exist; the browser keeps
  // a display cache (title/thumbnail) in the same localStorage keys as before.
  function syncLists(progress) {
    var localFav = load(FAVORITES_KEY, []);
    var localRecent = load(RECENT_KEY, []);
    if (!progress || !progress.player) {
      // No server player yet (nothing played since the cookie was issued):
      // keep showing what this browser already has.
      if (progress) { progress._fav = localFav; progress._recent = localRecent; }
      return Promise.resolve(progress);
    }
    // Import once per anonymous player (sync_id is a short hash, not the ID).
    var needImport = load(IMPORTED_KEY, '') !== progress.sync_id && (localFav.length || localRecent.length);
    var p = Promise.resolve(progress);
    if (needImport) {
      p = api('POST', {
        action: 'import',
        favorites: localFav.map(function (g) { return g && g.slug; }).filter(Boolean).slice(0, 200),
        recent: localRecent.map(function (g) { return g && g.slug; }).filter(Boolean).slice(0, 20)
      }).then(function (r) {
        if (r && r.ok && r.progress) { save(IMPORTED_KEY, progress.sync_id); return r.progress; }
        return null; // keep local lists untouched if the import failed
      }).catch(function () { return null; });
    } else {
      save(IMPORTED_KEY, progress.sync_id);
    }
    return p.then(function (pr) {
      if (!pr) { progress._fav = localFav; progress._recent = localRecent; return progress; }
      return Promise.all([resolveGames(pr.favorites), resolveGames(pr.recent)]).then(function (lists) {
        if (lists[0].length || pr.favorites.length === 0) save(FAVORITES_KEY, lists[0].map(stripHref));
        if (lists[1].length || pr.recent.length === 0) save(RECENT_KEY, lists[1].map(stripHref));
        try { window.dispatchEvent(new StorageEvent('storage', { key: FAVORITES_KEY })); } catch (e) { /* old browsers */ }
        pr._fav = lists[0];
        pr._recent = lists[1];
        return pr;
      });
    });
  }
  function stripHref(g) { return { slug: g.slug, title: g.title, category: g.category, thumbnail: g.thumbnail }; }

  function fillShelf(id, games) {
    var section = document.getElementById(id);
    var track = document.getElementById(id + '-track');
    if (!section || !track) return;
    if (!games || !games.length) { section.hidden = true; return; }
    track.innerHTML = games.slice(0, 20).map(cardHTML).join('');
    section.hidden = false;
    var nav = $('.shelf__nav', section);
    if (nav) nav.style.visibility = track.scrollWidth > track.clientWidth + 4 ? '' : 'hidden';
    if (!section.__spBound) {
      section.__spBound = true;
      var prev = $('[data-shelf-prev]', section), next = $('[data-shelf-next]', section);
      var go = function (dir) { track.scrollBy({ left: dir * Math.max(track.clientWidth * 0.85, 200), behavior: reducedMotion() ? 'auto' : 'smooth' }); };
      if (prev && !prev.__bound) prev.addEventListener('click', function () { go(-1); });
      if (next && !next.__bound) next.addEventListener('click', function () { go(1); });
    }
  }

  // ------------------------------------------------------------ renders --

  function fmtCountdown(iso) {
    var ms = Date.parse(iso) - Date.now();
    if (!(ms > 0)) return 'now';
    var d = Math.floor(ms / 86400000), h = Math.floor(ms % 86400000 / 3600000), m = Math.floor(ms % 3600000 / 60000);
    if (d > 0) return d + 'd ' + h + 'h';
    if (h > 0) return h + 'h ' + m + 'm';
    return Math.max(1, m) + 'm';
  }

  function levelHTML(pr) {
    var L = pr.level;
    var pct = Math.round((L.progress || 0) * 100);
    var nextTxt = L.next ? (L.into + ' / ' + L.span + ' XP to ' + esc(L.next.name)) : 'Top level reached';
    return '<div class="sp-level">' +
      '<div class="sp-level__badge" aria-hidden="true">' + esc(L.level) + '</div>' +
      '<div class="sp-level__text"><p class="sp-level__name">' + esc(pr.player ? pr.player.name : 'New player') + '</p>' +
      '<p class="sp-level__meta">Level ' + esc(L.level) + ' · ' + esc(L.name) + '</p></div></div>' +
      '<div class="sp-bar" role="progressbar" aria-label="Progress to next level" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + pct + '"><span style="width:' + pct + '%"></span></div>' +
      '<p class="sp-small">' + nextTxt + '</p>' +
      '<dl class="sp-stats"><div><dt>Lifetime XP</dt><dd>' + esc(pr.lifetime_xp) + '</dd></div><div><dt>This week</dt><dd>' + esc(pr.weekly_xp) + '</dd></div><div><dt title="Daily limit ' + esc(pr.rules.daily_cap) + ' XP">Today</dt><dd>' + esc(pr.today_xp) + '<small>/' + esc(pr.rules.daily_cap) + '</small></dd></div></dl>';
  }

  function weekHTML(pr) {
    var w = pr.week_progress;
    var dots = '';
    for (var i = 0; i < w.target; i++) dots += '<span class="sp-dot' + (i < w.count ? ' is-on' : '') + '" aria-hidden="true"></span>';
    var bonus = w.bonus_awarded ? '<strong>+' + esc(w.bonus_xp) + ' XP weekly bonus earned</strong>' :
      'Play on ' + esc(w.target) + ' different days this week for <strong>+' + esc(w.bonus_xp) + ' XP</strong>';
    var cb = pr.comeback;
    var cbTxt = cb.claimed_today ? '<span class="sp-ok">✓</span> Comeback reward collected today (+' + esc(cb.xp) + ' XP).' :
      cb.available ? 'Play any game for 60 seconds today to collect your <strong>+' + esc(cb.xp) + ' XP</strong> comeback reward.' :
        'Come back on another day and play for 60 seconds to earn <strong>+' + esc(cb.xp) + ' XP</strong>.';
    return '<div class="sp-week"><div class="sp-dots" role="img" aria-label="' + esc(w.count) + ' of ' + esc(w.target) + ' active days">' + dots + '</div>' +
      '<p class="sp-week__count">' + esc(Math.min(w.count, w.target)) + '/' + esc(w.target) + ' active days</p></div>' +
      '<p class="sp-small">' + bonus + '</p><p class="sp-small sp-comeback">' + cbTxt + '</p>' +
      '<p class="sp-small sp-muted">Week resets Monday 00:00 UTC (in <span data-sp-cd="' + esc(pr.weekly_resets_at) + '">' + fmtCountdown(pr.weekly_resets_at) + '</span>). Missing days never costs you anything.</p>';
  }

  function questsHTML(list, resetsAt, label) {
    return '<ul class="sp-quests">' + list.map(function (q) {
      var state = q.done ? 'done' : 'pending';
      return '<li class="sp-quest sp-quest--' + state + '" data-quest="' + esc(q.id) + '">' +
        '<span class="sp-quest__icon" aria-hidden="true">' + (q.done ? '✓' : '○') + '</span>' +
        '<span class="sp-quest__text">' + esc(q.title) + '<small>' + esc(q.progress) + '/' + esc(q.target) + ' · ' + (q.done ? 'Completed' : 'Pending') + '</small></span>' +
        '<span class="sp-quest__xp">+' + esc(q.xp) + ' XP</span></li>';
    }).join('') + '</ul><p class="sp-small sp-muted">' + esc(label) + ' reset in <span data-sp-cd="' + esc(resetsAt) + '">' + fmtCountdown(resetsAt) + '</span> (00:00 UTC).</p>';
  }

  function leagueHTML(lb, compact) {
    if (!lb) return '<p class="sp-small sp-muted">League unavailable right now.</p>';
    var rows = lb.top || [];
    var body;
    if (!rows.length) {
      body = '<p class="sp-small">No one has earned XP this week yet. Play a game for a minute to be first!</p>';
    } else {
      var podium = rows.slice(0, 3);
      body = '<ol class="sp-podium">' + podium.map(function (r) {
        return '<li class="sp-podium__p sp-podium__p' + r.rank + (r.you ? ' is-you' : '') + '"><span class="sp-podium__medal" aria-hidden="true">' + (['🥇', '🥈', '🥉'][r.rank - 1] || '') + '</span>' +
          '<span class="sp-podium__name">' + esc(r.name) + (r.you ? ' <em>(you)</em>' : '') + '</span><span class="sp-podium__xp">' + esc(r.xp) + ' XP</span></li>';
      }).join('') + '</ol>';
      if (!compact && rows.length > 3) {
        body += '<ol class="sp-board" start="4">' + rows.slice(3).map(function (r) {
          return '<li class="' + (r.you ? 'is-you' : '') + '"><span class="sp-board__rank">' + esc(r.rank) + '</span><span class="sp-board__name">' + esc(r.name) + (r.you ? ' <em>(you)</em>' : '') +
            '<small>' + esc(r.level) + '</small></span><span class="sp-board__xp">' + esc(r.xp) + ' XP</span></li>';
        }).join('') + '</ol>';
      }
    }
    var me = lb.me ? '<p class="sp-me">Your rank: <strong>#' + esc(lb.me.rank) + '</strong> of ' + esc(lb.players) + ' · ' + esc(lb.me.xp) + ' XP this week</p>' :
      '<p class="sp-me sp-muted">Earn XP this week to get a rank.</p>';
    return body + me + '<p class="sp-small sp-muted">Resets Monday 00:00 UTC (in <span data-sp-cd="' + esc(lb.resets_at) + '">' + fmtCountdown(lb.resets_at) + '</span>). Just for fun: no prizes, nothing to buy.</p>';
  }

  function tickCountdowns() {
    $$('[data-sp-cd]').forEach(function (el) { el.textContent = fmtCountdown(el.getAttribute('data-sp-cd')); });
  }

  function observeOnce(el, cb) {
    if (!el) return;
    if (!('IntersectionObserver' in window)) { cb(); return; }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) { io.disconnect(); cb(); } });
    }, { threshold: 0.5 });
    io.observe(el);
  }

  // ------------------------------------------------------------ homepage --

  function initHome() {
    var section = $('#sprout-journey');
    if (!section) return;
    api('GET', null, '?lb=3').then(function (res) {
      if (!res || !res.enabled || res.optout || !res.progress) return;
      return syncLists(res.progress).then(function (pr) {
        fillShelf('continue-playing', pr._recent);
        fillShelf('your-favourites', pr._fav);
        if (!pr.active) return; // brand-new visitors: keep the homepage unchanged
        var dq = pr.quests.daily;
        var welcome = '';
        var seen = load(SEEN_COMEBACK_KEY, '');
        if (pr.comeback.available && !pr.comeback.claimed_today && seen !== pr.day) {
          welcome = '<div class="sj__welcome"><span aria-hidden="true">🌱</span> Welcome back! Play any game for 60 seconds today to collect <strong>+' + esc(pr.comeback.xp) + ' Sprout XP</strong>.</div>';
        }
        $('[data-sj-body]', section).innerHTML = welcome +
          '<div class="sj__grid">' +
          '<div class="sj__col">' + levelHTML(pr) + '</div>' +
          '<div class="sj__col"><h3 class="sj__h">This week</h3>' + weekHTML(pr) + '</div>' +
          '<div class="sj__col"><h3 class="sj__h">Daily quests</h3>' + questsHTML(dq, pr.daily_resets_at, 'Daily quests') + '</div>' +
          '<div class="sj__col sj__col--league"><h3 class="sj__h">Sprout League</h3>' + leagueHTML(res.leaderboard, true) + '<a class="sj__link" href="/rewards/">Full league &amp; weekly quests →</a></div>' +
          '</div>';
        section.hidden = false;
        // Returning players: show it right after the homepage hero (built
        // asynchronously by redesign.js). If the hero never appears, it stays
        // in its server-rendered spot.
        var waited = 0;
        (function place() {
          var hero = $('.rd-home > .rd-hero');
          if (hero) { hero.parentNode.insertBefore(section, hero.nextSibling); return; }
          if ((waited += 200) <= 4000) setTimeout(place, 200);
        })();
        observeOnce($('.sp-podium, .sp-me', section), function () { ga('leaderboard_viewed', { leaderboard_view: 'home_preview' }); });
        setInterval(tickCountdowns, 30000);
      });
    }).catch(function () {});
  }

  // -------------------------------------------------------- rewards page --

  function initRewards() {
    var page = $('[data-sprout-page="rewards"]');
    if (!page) return;
    var status = $('[data-sp-status]', page);
    var resumeBtn = $('[data-sp-resume]', page);
    function paint(res) {
      if (!res || res.__status >= 500 || !res.enabled) {
        status.textContent = res && res.enabled === false ? 'Rewards are not switched on yet. Check back soon!' : 'Progress is unavailable right now. Your games still work as normal.';
        return;
      }
      if (res.optout) {
        status.textContent = 'Progress tracking is off for this browser. Nothing is stored about you.';
        if (resumeBtn) resumeBtn.hidden = false;
        $$('[data-sp-forget],[data-sp-optout]', page).forEach(function (b) { b.hidden = true; });
        return;
      }
      var pr = res.progress;
      syncLists(pr).then(function (p2) {
        fillShelf('continue-playing', p2._recent);
        fillShelf('your-favourites', p2._fav);
      }).catch(function () {});
      status.textContent = pr.active ? '' : 'Play any game for 60 seconds to start earning Sprout XP. No account needed.';
      status.hidden = !status.textContent;
      $('[data-sp-you]', page).innerHTML = levelHTML(pr) +
        '<ol class="sp-levels">' + pr.rules.levels.map(function (l) {
          return '<li class="' + (l.level <= pr.level.level ? 'is-on' : '') + '"><b>' + esc(l.name) + '</b><small>' + esc(l.min) + ' XP</small></li>';
        }).join('') + '</ol>';
      $('[data-sp-week]', page).innerHTML = weekHTML(pr);
      $('[data-sp-daily]', page).innerHTML = questsHTML(pr.quests.daily, pr.daily_resets_at, 'Daily quests');
      $('[data-sp-weekly]', page).innerHTML = questsHTML(pr.quests.weekly, pr.weekly_resets_at, 'Weekly quests');
      $('[data-sp-league]', page).innerHTML = leagueHTML(res.leaderboard, false);
      ga('leaderboard_viewed', { leaderboard_view: 'rewards_page' });
    }
    api('GET', null, '?lb=10').then(paint).catch(function () { paint(null); });
    setInterval(tickCountdowns, 30000);

    function forget(optout) {
      var msg = optout ? 'Delete all progress for this browser and stop tracking? Favourites and recently played on this browser will also be cleared.' :
        'Delete all progress (XP, quests, favourites, recently played) for this browser and start fresh?';
      if (!window.confirm(msg)) return;
      api('POST', { action: 'forget', optout: !!optout }).then(function (r) {
        if (!r || !r.ok) { notify('<p class="sp-toast__sub">Could not reset right now. Please try again.</p>'); return; }
        save(FAVORITES_KEY, []); save(RECENT_KEY, []); save(IMPORTED_KEY, '');
        window.location.reload();
      }).catch(function () {});
    }
    var f = $('[data-sp-forget]', page), o = $('[data-sp-optout]', page);
    if (f) f.addEventListener('click', function () { forget(false); });
    if (o) o.addEventListener('click', function () { forget(true); });
    if (resumeBtn) resumeBtn.addEventListener('click', function () {
      api('POST', { action: 'resume' }).then(function () { window.location.reload(); }).catch(function () {});
    });
  }

  // -------------------------------------------------- game session meter --

  function gameContext() {
    var b = document.body;
    if (b.getAttribute('data-page') === 'game' && b.getAttribute('data-slug')) {
      return { slug: b.getAttribute('data-slug'), title: b.getAttribute('data-title') || document.title, category: b.getAttribute('data-genre') || '' };
    }
    var tool = b.getAttribute('data-tool-slug');
    var m = location.pathname.match(/^\/games\/([a-z0-9-]+)\.html$/);
    var slug = tool || (m && m[1]);
    if (!slug || !$('.game-title, .game-bar__title, h1')) return null;
    var t = $('.game-title, .game-bar__title, h1');
    return { slug: slug, title: t ? t.textContent.trim() : document.title, category: tool ? 'Originals' : (($('.game-meta') || {}).textContent || '').trim() };
  }

  function initGame() {
    var ctx = gameContext();
    if (!ctx) return;
    var frame = $('.stage iframe, .embed-frame-wrap iframe, .game-board-frame iframe, .game-shell iframe, main iframe');
    var gaBase = { game_title: ctx.title, game_category: ctx.category };

    // game_loaded: the embedded game's document finished loading. For
    // cross-origin embeds this proves the frame loaded, not that it played.
    if (frame) frame.addEventListener('load', function () { ga('game_loaded', gaBase); }, { once: true });

    var started = false, active = 0, lastInput = 0, sent = false, tries = 0;
    function markStart() {
      lastInput = Date.now();
      if (started) return;
      started = true;
      ga('game_start', gaBase); // first real interaction with the game, not a pageview
    }
    ['pointerdown', 'keydown', 'touchstart', 'wheel'].forEach(function (t) {
      window.addEventListener(t, function (e) {
        var target = e.target;
        if (target && target.closest && target.closest('.topbar, .rail, .footer, .overlay, .shelf, .game-rail')) return;
        markStart();
      }, { passive: true, capture: true });
    });
    // Clicking into a cross-origin iframe blurs this window and focuses the frame.
    window.addEventListener('blur', function () {
      setTimeout(function () { if (frame && document.activeElement === frame) markStart(); }, 0);
    });

    function isActive() {
      if (!started || document.visibilityState !== 'visible') return false;
      if (frame && document.activeElement === frame && document.hasFocus()) return true; // playing inside the embed
      return Date.now() - lastInput < 15000 && document.hasFocus();
    }

    var done = false, activeFired = false;
    function qualify() {
      if (sent || done) return;
      sent = true;
      api('POST', { action: 'qualify', slug: ctx.slug }).then(function (r) {
        if (r && r.ok) { done = true; announceAwards(r, ctx); return; }
        // The server clock says 60 s have not passed yet: keep measuring.
        if (r && r.reason === 'too_early' && tries < 3) { tries++; sent = false; active = Math.max(0, active - 10); return; }
        done = true;
      }).catch(function () { if (tries++ < 3) sent = false; else done = true; });
    }

    api('POST', { action: 'open', slug: ctx.slug }).then(function (r) {
      if (!r || !r.ok) return; // disabled, opted out, unknown game or rate limited: no tracking
      if (r.player_created) ga('anonymous_player_created', {});
      var need = r.qualifying_seconds || 60;
      var timer = setInterval(function () {
        if (done) { clearInterval(timer); return; }
        if (sent) return;
        if (isActive()) active++;
        if (active >= need) {
          if (!activeFired) { activeFired = true; ga('game_active_60s', gaBase); }
          qualify();
        }
      }, 1000);
    }).catch(function () {});
  }

  // -------------------------------------------------- Pixelsprout Picks --

  var picksP = null;
  function picksData() {
    if (!picksP) picksP = fetch('/assets/picks/picks.json').then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    return picksP;
  }

  function preferredStore(data) {
    var s = load(STORE_KEY, '');
    if (s && data.stores[s]) return s;
    var tz = '';
    try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { /* ignore */ }
    if (/^Asia\/(Kolkata|Calcutta)$/.test(tz)) return 'IN';
    return data.stores.US ? 'US' : data.default_store;
  }

  function searchUrl(store, q) {
    return 'https://' + store.domain + '/s?k=' + encodeURIComponent(q).replace(/%20/g, '+') + '&tag=' + encodeURIComponent(store.tag);
  }

  function sourceInfo() {
    var b = document.body;
    return { source_page: b.getAttribute('data-page') || location.pathname.split('/')[1] || 'home', game_category: b.getAttribute('data-genre') || '' };
  }

  function applyStore(root, data, code) {
    var store = data.stores[code];
    $$('[data-aff="category"]', root).forEach(function (a) {
      a.href = searchUrl(store, a.getAttribute('data-aff-q'));
      a.setAttribute('data-aff-store', code);
    });
    $$('[data-pick-store]', root).forEach(function (card) { card.hidden = card.getAttribute('data-pick-store') !== code; });
    $$('.picks__store', root).forEach(function (b) { b.setAttribute('aria-pressed', b.getAttribute('data-store') === code ? 'true' : 'false'); });
  }

  function wirePicks(root, data) {
    if (root.__spPicks) return;
    root.__spPicks = true;
    var code = preferredStore(data);
    applyStore(root, data, code);
    $$('.picks__store', root).forEach(function (b) {
      b.addEventListener('click', function () {
        code = b.getAttribute('data-store');
        save(STORE_KEY, code);
        applyStore(root, data, code);
      });
    });
    var src = sourceInfo();
    var cards = $$('.pick', root).filter(function (c) { return !c.hidden; });
    observeOnce(root, function () {
      ga('affiliate_recommendation_impression', { source_page: src.source_page, game_category: src.game_category, item_count: cards.length, placement: root.getAttribute('data-picks') });
    });
    cards.forEach(function (card) {
      observeOnce(card, function () {
        if (card.hidden) return;
        ga('affiliate_product_view', { product_category: card.getAttribute('data-pick'), source_page: src.source_page, game_category: src.game_category, placement: root.getAttribute('data-picks') });
      });
    });
    // Links go straight to Amazon; the event is sent with a beacon and the
    // click is never delayed, redirected or rewritten.
    root.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('a[data-aff]');
      if (!a) return;
      ga('affiliate_link_click', {
        product_category: a.getAttribute('data-aff-cat'), link_type: a.getAttribute('data-aff'), store: a.getAttribute('data-aff-store'),
        source_page: src.source_page, game_category: src.game_category, placement: root.getAttribute('data-picks'), transport_type: 'beacon'
      });
    });
  }

  function initPicks() {
    var roots = $$('[data-picks]');
    var ctx = document.body.getAttribute('data-page') === 'game' ? document.body.getAttribute('data-genre') : null;
    if (!roots.length && !ctx) return;
    picksData().then(function (data) {
      if (!data) return;
      roots.forEach(function (r) { wirePicks(r, data); });
      if (ctx) contextualPick(data, ctx);
    });
  }

  // One contextual recommendation below the game's description. Never shown
  // on children's categories and never inside or over the game.
  function contextualPick(data, genre) {
    if ((data.excluded_game_categories || []).indexOf(genre) >= 0) return;
    var about = $('.game-about');
    if (!about || $('[data-picks="game"]')) return;
    var catId = data.contextual[genre] || data.contextual['default'];
    var cat = null;
    data.categories.forEach(function (c) { if (c.id === catId) cat = c; });
    if (!cat) return;
    var code = preferredStore(data);
    var store = data.stores[code];
    var sec = document.createElement('section');
    sec.className = 'picks picks--game';
    sec.setAttribute('data-picks', 'game');
    sec.setAttribute('aria-labelledby', 'picks-game-title');
    sec.innerHTML = '<div class="picks__head"><h2 class="picks__title" id="picks-game-title">Pixelsprout Picks</h2><a class="shelf__more" href="/picks/">More picks</a></div>' +
      '<article class="pick pick--row" data-pick="' + esc(cat.id) + '"><div class="pick__art" aria-hidden="true">' + esc(cat.art) + '</div><div class="pick__body">' +
      '<p class="pick__cat">' + esc(cat.name) + '</p><h3 class="pick__name">Browse ' + esc(cat.name.toLowerCase()) + '</h3><p class="pick__desc">' + esc(cat.description) + '</p>' +
      '<div class="pick__foot"><a class="pick__cta" href="' + esc(searchUrl(store, cat.query)) + '" target="_blank" rel="sponsored noopener" data-aff="category" data-aff-cat="' + esc(cat.id) + '" data-aff-store="' + esc(code) + '" data-aff-q="' + esc(cat.query) + '">View on Amazon<span class="visually-hidden"> (opens Amazon in a new tab)</span></a><span class="pick__paid">(paid link)</span></div></div></article>' +
      '<p class="picks__disclosure picks__disclosure--sm">' + esc(data.disclosure) + '</p>';
    about.parentNode.insertBefore(sec, about.nextSibling);
    ensureCss();
    wirePicks(sec, data);
  }

  // --------------------------------------------------------- navigation --

  function addRailLink() {
    var rail = $('#rail');
    if (!rail || $('a[href="/rewards/"]', rail)) return;
    var anchor = $('[data-open-panel="recent"]', rail);
    if (!anchor) return;
    var a = document.createElement('a');
    a.className = 'rail__link';
    a.href = '/rewards/';
    a.setAttribute('aria-label', 'Rewards');
    a.title = 'Rewards';
    if (location.pathname === '/rewards/') a.setAttribute('aria-current', 'page');
    a.innerHTML = '<span class="i sp-rail-icon" aria-hidden="true">🌱</span><span class="rail__text">Rewards</span>';
    anchor.parentNode.insertBefore(a, anchor.nextSibling);
  }

  function init() {
    addRailLink();
    initHome();
    initRewards();
    initGame();
    initPicks();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
