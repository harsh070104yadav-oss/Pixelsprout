/*
 * Sandboxed Adsterra slot loader.
 * Every Adsterra script (native + banners) runs strictly inside a sandboxed
 * iframe WITHOUT allow-top-navigation or allow-top-navigation-by-user-activation,
 * so no ad creative can ever redirect or hijack the top-level page.
 */
(function () {
  'use strict';

  var BANNER_HOST = 'https://www.highperformanceformat.com/';
  var EMPTY_AFTER = 12000;
  var SANDBOX = 'allow-scripts allow-same-origin allow-popups';

  function buildSandboxedFrame(html, w, h) {
    var f = document.createElement('iframe');
    f.setAttribute('sandbox', SANDBOX);
    f.setAttribute('referrerpolicy', 'no-referrer-when-downgrade');
    f.setAttribute('loading', 'lazy');
    f.setAttribute('scrolling', 'no');
    f.setAttribute('frameborder', '0');
    f.setAttribute('title', 'Advertisement');
    f.style.cssText = 'border:0;display:block;margin:0 auto;overflow:hidden;background:transparent;' +
      (w ? 'width:' + w + 'px;max-width:100%;' : 'width:100%;') +
      (h ? 'height:' + h + 'px;' : 'min-height:250px;height:260px;');
    f.srcdoc = '<!doctype html><html><head><meta charset="utf-8"><base target="_blank">' +
      '<style>html,body{margin:0;padding:0;overflow:hidden;background:transparent;display:flex;justify-content:center;align-items:flex-start;}</style>' +
      '</head><body>' + html + '</body></html>';
    return f;
  }

  function pickSize(slot) {
    var minWidth = parseInt(slot.getAttribute('data-min-width') || '0', 10);
    if (!minWidth || window.matchMedia('(min-width: ' + minWidth + 'px)').matches) {
      return { key: slot.getAttribute('data-key'), w: +slot.getAttribute('data-w'), h: +slot.getAttribute('data-h') };
    }
    var altKey = slot.getAttribute('data-alt-key');
    if (altKey) {
      slot.classList.add('is-alt');
      return { key: altKey, w: +slot.getAttribute('data-alt-w'), h: +slot.getAttribute('data-alt-h') };
    }
    return null;
  }

  function loadBanner(slot) {
    var size = pickSize(slot);
    if (!size || !size.key || !/^[a-f0-9]{16,64}$/i.test(size.key)) {
      slot.classList.add('is-skipped');
      return;
    }
    var host = slot.querySelector('.ad__slot') || slot;
    var html = '<script>window.atOptions={key:"' + size.key + '",format:"iframe",height:' + size.h + ',width:' + size.w + ',params:{}};<\/script>' +
      '<script src="' + BANNER_HOST + size.key + '/invoke.js"><\/script>';
    host.appendChild(buildSandboxedFrame(html, size.w, size.h));
  }

  function loadNative(slot) {
    var src = slot.getAttribute('data-src');
    if (!src || !/^https:\/\/[a-z0-9.-]+\.effectivecpmnetwork\.com\/[a-f0-9]+\/invoke\.js$/i.test(src)) return;
    var container = slot.querySelector('div[id^="container-"]');
    var cid = container ? container.id : 'container-c3f3d41bbefde3a25a4d5d341b5582d1';
    var html = '<script async data-cfasync="false" src="' + src + '"><\/script><div id="' + cid + '"></div>';
    var host = container || slot;
    host.innerHTML = '';
    host.appendChild(buildSandboxedFrame(html, 0, 260));
  }

  function activate(slot) {
    if (slot.getAttribute('data-ad-state')) return;
    slot.setAttribute('data-ad-state', 'loading');
    var kind = slot.getAttribute('data-ad');
    if (kind === 'native') {
      loadNative(slot);
    } else if (kind === 'banner') {
      loadBanner(slot);
    }
  }

  function init() {
    var slots = Array.prototype.slice.call(document.querySelectorAll('.ad[data-ad]'));
    if (!slots.length) return;
    if (!('IntersectionObserver' in window)) {
      slots.forEach(activate);
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          io.unobserve(entry.target);
          activate(entry.target);
        }
      });
    }, { rootMargin: '200px 0px' });
    slots.forEach(function (slot) { io.observe(slot); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

/* Pixel Sprout Originals: shelf + menu link, driven by /assets/originals.json */
(function () {
  function recount(section) {
    var count = section.querySelector('.shelf__count');
    var left = section.querySelectorAll('a.card').length;
    if (count) count.textContent = left + (left === 1 ? ' game' : ' games');
    if (!left) section.remove();
  }
  function boot() {
    var labels = document.querySelectorAll('.rail__label');
    var label = null;
    for (var i = 0; i < labels.length; i++) {
      if (labels[i].textContent.replace(/\s+/g, ' ').trim() === 'Originals') label = labels[i];
    }
    if (label && !document.querySelector('a.rail__link[href="/originals/"]')) {
      var a = document.createElement('a');
      a.className = 'rail__link';
      a.href = '/originals/';
      a.setAttribute('aria-label', 'Pixel Sprout Originals');
      a.innerHTML = '<svg class="i" aria-hidden="true"><use href="/assets/icons.svg#sparkle"></use></svg><span class="rail__text">Pixel Sprout Originals</span>';
      label.insertAdjacentElement('afterend', a);
    }
    if (!document.body.classList.contains('home-page') || document.getElementById('pixel-sprout-originals')) return;
    fetch('/assets/originals.json').then(function (r) { return r.ok ? r.json() : []; }).then(function (games) {
      if (!games || !games.length || document.getElementById('pixel-sprout-originals')) return;
      games = games.filter(function (g) { return g && g.thumbnail; });
      if (!games.length) return;
      var cards = games.map(function (g) {
        var title = String(g.title || '');
        var slug = String(g.slug || '');
        var cat = String(g.category || '');
        var thumb = String(g.thumbnail || '');
        var href = g.href ? String(g.href) : ('/games/' + slug + '.html');
        return '<a class="card" href="' + href + '" data-name="' + title.toLowerCase() + '">' +
          '<span class="card__media"><img src="' + thumb + '" alt="' + title + '" width="320" height="320" loading="lazy" decoding="async"></span>' +
          '<span class="card__title">' + title + '</span>' +
          (cat ? '<span class="card__meta">' + cat + '</span>' : '') +
          '</a>';
      }).join('');
      var n = games.length;
      var section = document.createElement('section');
      section.className = 'shelf shelf--originals';
      section.id = 'pixel-sprout-originals';
      section.setAttribute('aria-labelledby', 'pixel-sprout-originals-title');
      section.innerHTML = '<div class="shelf__head"><h2 class="shelf__title" id="pixel-sprout-originals-title">Pixel Sprout Originals</h2>' +
        '<span class="shelf__count">' + n + (n === 1 ? ' game' : ' games') + '</span>' +
        '<span class="shelf__spacer"></span><a class="shelf__more" href="/originals/">See all</a></div>' +
        '<div class="shelf__track" data-shelf-track>' + cards + '</div>';
      section.querySelectorAll('.card__media img').forEach(function (img) {
        img.addEventListener('error', function () {
          var card = img.closest('a.card');
          if (card) card.remove();
          recount(section);
        });
      });
      var anchor = document.querySelector('.hero') || document.querySelector('.home-intro');
      if (anchor) anchor.insertAdjacentElement('afterend', section);
    }).catch(function () {});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
