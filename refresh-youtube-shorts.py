#!/usr/bin/env python3
"""
Refresh the homepage "Gameplay Shorts" shelf from @Playpixelsprout's public YouTube RSS feed.
- Fetches https://www.youtube.com/feeds/videos.xml?channel_id=UCn2DZlF_8y3rvLZUvD80mSg
- Matches each Short to a live Pixelsprout game page when possible
- Writes assets/youtube-shorts.json (static fallback, zero client CORS)
- Bakes a click-to-play horizontal scrollable shelf into index.html below the fold
  (zero YouTube iframe/JS loaded until a visitor clicks a card).
"""
import html as h
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(__file__))
CHANNEL_ID = "UCn2DZlF_8y3rvLZUvD80mSg"
RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
JSON_OUT = os.path.join(ROOT, "assets", "youtube-shorts.json")
INDEX_HTML = os.path.join(ROOT, "index.html")
GAMES_INDEX = os.path.join(ROOT, "games-index.json")

# Explicit high-confidence title keyword -> (slug, display title)
KEYWORD_MAP = [
    (r"protect my dog|draw nothing|did the roof hold|5 lines,\s*5 fails|2 seconds from safe", "protect-my-dog", "Protect My Dog"),
    (r"falling dummy|brutal fall|worst fall|damage without falling", "falling-dummy", "Falling Dummy"),
    (r"granny watch|granny house|granny vs", "granny-house-escape", "Granny House Escape"),
    (r"scare watch|the house 2|house 2", "the-house-2", "The House 2"),
    (r"scary doll", "scary-doll", "Scary Doll"),
    (r"backrooms", "boo-scared-escape-from-backrooms", "Boo Scared? Escape from Backrooms"),
    (r"slope racing|138m on run", "slope-racing-3d", "Slope Racing 3D"),
    (r"getting over it", "getting-over-it", "Getting Over It"),
    (r"bottle flip|0 on the table", "bottle-flip-game", "Bottle Flip Game"),
    (r"books tower|book 11 hold", "books-tower", "Books Tower"),
    (r"escape from work|worst glitch in a browser game", "escape-from-work", "Escape From Work"),
    (r"geometrical dash|attempt 1 vs attempt", "geometrical-dash", "Geometrical Dash"),
    (r"stack ball crash", "stack-ball-crash", "Stack Ball Crash"),
]


def load_games_lookup():
    if not os.path.exists(GAMES_INDEX):
        return []
    try:
        data = json.load(open(GAMES_INDEX, encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("games", [])
        out = []
        for g in items:
            title = (g.get("title") or g.get("name") or "").strip()
            slug = (g.get("slug") or "").strip()
            if title and slug and len(title) >= 5:
                out.append((title.lower(), slug, title))
        out.sort(key=lambda x: len(x[0]), reverse=True)
        return out
    except Exception:
        return []


def match_game(title, games_lookup):
    low = title.lower()
    for pat, slug, gname in KEYWORD_MAP:
        if re.search(pat, low):
            if os.path.exists(os.path.join(ROOT, "games", f"{slug}.html")):
                return f"/games/{slug}.html", gname
    for g_low, slug, gname in games_lookup:
        if g_low in low and os.path.exists(os.path.join(ROOT, "games", f"{slug}.html")):
            return f"/games/{slug}.html", gname
    return "", ""


def fetch_shorts(limit=12):
    req = urllib.request.Request(RSS_URL, headers={"User-Agent": "Mozilla/5.0"})
    rss = urllib.request.urlopen(req, timeout=15).read()
    root = ET.fromstring(rss)
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    games_lookup = load_games_lookup()
    items = []
    for entry in root.findall("atom:entry", ns)[:limit]:
        vid_el = entry.find("yt:videoId", ns)
        title_el = entry.find("atom:title", ns)
        pub_el = entry.find("atom:published", ns)
        if vid_el is None or not vid_el.text:
            continue
        vid = vid_el.text.strip()
        raw_title = (title_el.text or "").strip() if title_el is not None else "Pixelsprout Gameplay"
        clean_title = re.sub(r"\s*#shorts\b", "", raw_title, flags=re.I).strip() or raw_title
        pub = (pub_el.text or "")[:10] if pub_el is not None else ""
        game_url, game_title = match_game(clean_title, games_lookup)
        items.append({
            "id": vid,
            "title": clean_title,
            "published": pub,
            "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            "url": f"https://www.youtube.com/shorts/{vid}",
            "gameUrl": game_url,
            "gameTitle": game_title,
        })
    return items


def render_shelf(items):
    cards = []
    for it in items:
        vid = h.escape(it["id"], quote=True)
        title = h.escape(it["title"], quote=True)
        thumb = h.escape(it["thumb"], quote=True)
        gurl = h.escape(it["gameUrl"], quote=True)
        gtitle = h.escape(it["gameTitle"], quote=True)
        play_badge = (
            f'<a class="short-card__game" href="{gurl}" onclick="event.stopPropagation()">Play {gtitle} →</a>'
            if gurl else '<span class="short-card__tag">Gameplay Short</span>'
        )
        cards.append(
            f'<article class="short-card">'
            f'<button type="button" class="short-card__btn" data-short-id="{vid}" data-short-title="{title}" '
            f'data-game-url="{gurl}" data-game-title="{gtitle}" aria-label="Watch {title}">'
            f'<span class="short-card__media">'
            f'<img src="{thumb}" alt="{title}" width="480" height="360" loading="lazy" decoding="async">'
            f'<span class="short-card__play" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></span>'
            f'</span>'
            f'<span class="short-card__title">{title}</span>'
            f'</button>'
            f'{play_badge}'
            f'</article>'
        )

    return (
        '<!--ps:yt-shorts-shelf-->'
        '<section class="shelf shelf--shorts" id="gameplay-shorts" aria-labelledby="gameplay-shorts-title">'
        '<style>'
        '.shelf--shorts{margin-top:var(--sp-6,24px);}'
        '.shelf--shorts .shorts-track{display:flex;gap:14px;overflow-x:auto;scroll-snap-type:x mandatory;padding:4px 2px 10px;-webkit-overflow-scrolling:touch;scrollbar-width:thin;}'
        '.short-card{flex:0 0 168px;scroll-snap-align:start;display:flex;flex-direction:column;gap:8px;background:var(--surface,#161822);border:1px solid var(--border,rgba(255,255,255,.1));border-radius:14px;padding:8px;transition:transform .15s ease,border-color .15s ease;}'
        '.short-card:hover{transform:translateY(-2px);border-color:rgba(124,156,255,.45);}'
        '.short-card__btn{all:unset;cursor:pointer;display:flex;flex-direction:column;gap:8px;width:100%;}'
        '.short-card__media{position:relative;width:100%;aspect-ratio:9/16;border-radius:10px;overflow:hidden;background:#000;display:block;}'
        '.short-card__media img{width:100%;height:100%;object-fit:cover;display:block;}'
        '.short-card__play{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:48px;height:34px;border-radius:9px;background:rgba(255,0,0,.92);display:flex;align-items:center;justify-content:center;box-shadow:0 4px 14px rgba(0,0,0,.5);}'
        '.short-card__play svg{width:20px;height:20px;fill:#fff;margin-left:2px;}'
        '.short-card__title{font-size:.86rem;font-weight:600;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:2.6em;}'
        '.short-card__game{font-size:.78rem;font-weight:600;color:var(--accent,#7c9cff);text-decoration:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}'
        '.short-card__game:hover{text-decoration:underline;}'
        '.short-card__tag{font-size:.75rem;opacity:.65;}'
        '.shorts-modal{position:fixed;inset:0;z-index:9999;background:rgba(8,10,16,.84);backdrop-filter:blur(4px);display:none;align-items:center;justify-content:center;padding:16px;}'
        '.shorts-modal.is-open{display:flex;}'
        '.shorts-modal__box{width:100%;max-width:360px;background:#121520;border:1px solid rgba(255,255,255,.14);border-radius:16px;padding:14px;display:flex;flex-direction:column;gap:12px;box-shadow:0 20px 60px rgba(0,0,0,.6);}'
        '.shorts-modal__top{display:flex;align-items:center;justify-content:space-between;gap:10px;}'
        '.shorts-modal__title{margin:0;font-size:.95rem;font-weight:600;line-height:1.3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}'
        '.shorts-modal__close{all:unset;cursor:pointer;width:30px;height:30px;border-radius:8px;background:rgba(255,255,255,.1);display:flex;align-items:center;justify-content:center;font-size:1.1rem;flex-shrink:0;}'
        '.shorts-modal__player{position:relative;width:100%;aspect-ratio:9/16;max-height:68vh;border-radius:12px;overflow:hidden;background:#000;}'
        '.shorts-modal__player iframe{position:absolute;inset:0;width:100%;height:100%;border:0;}'
        '.shorts-modal__actions{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;}'
        '.shorts-modal__cta{display:inline-flex;align-items:center;justify-content:center;padding:9px 14px;border-radius:10px;background:#5b7cfa;color:#fff;font-weight:700;font-size:.88rem;text-decoration:none;flex:1;text-align:center;}'
        '.shorts-modal__cta[hidden]{display:none;}'
        '.shorts-modal__yt{font-size:.82rem;color:#b8c4ff;text-decoration:none;}'
        '@media(max-width:520px){.short-card{flex:0 0 148px;}}'
        '</style>'
        '<div class="shelf__head">'
        '<h2 class="shelf__title" id="gameplay-shorts-title">Gameplay Shorts</h2>'
        f'<span class="shelf__count">{len(items)} clips</span>'
        '<span class="shelf__spacer"></span>'
        '<a class="shelf__more" href="https://www.youtube.com/@Playpixelsprout/shorts" target="_blank" rel="noopener">See all on YouTube ↗</a>'
        '</div>'
        '<div class="shorts-track" role="list">' + "".join(cards) + '</div>'
        '<div class="shorts-modal" id="yt-shorts-modal" role="dialog" aria-modal="true" aria-labelledby="yt-shorts-modal-title">'
        '<div class="shorts-modal__box">'
        '<div class="shorts-modal__top">'
        '<h3 class="shorts-modal__title" id="yt-shorts-modal-title">Gameplay Short</h3>'
        '<button type="button" class="shorts-modal__close" id="yt-shorts-modal-close" aria-label="Close video">×</button>'
        '</div>'
        '<div class="shorts-modal__player" id="yt-shorts-modal-player"></div>'
        '<div class="shorts-modal__actions">'
        '<a class="shorts-modal__cta" id="yt-shorts-modal-cta" href="#" hidden>Play This Game</a>'
        '<a class="shorts-modal__yt" id="yt-shorts-modal-yt" href="https://www.youtube.com/@Playpixelsprout/shorts" target="_blank" rel="noopener">Open on YouTube ↗</a>'
        '</div>'
        '</div>'
        '</div>'
        '<script>(function(){'
        'var m=document.getElementById("yt-shorts-modal"),p=document.getElementById("yt-shorts-modal-player"),'
        't=document.getElementById("yt-shorts-modal-title"),c=document.getElementById("yt-shorts-modal-cta"),'
        'y=document.getElementById("yt-shorts-modal-yt"),x=document.getElementById("yt-shorts-modal-close");'
        'if(!m||!p)return;'
        'function close(){m.classList.remove("is-open");p.innerHTML="";}'
        'document.querySelectorAll("#gameplay-shorts .short-card__btn").forEach(function(b){'
        'b.addEventListener("click",function(){'
        'var id=b.getAttribute("data-short-id"),title=b.getAttribute("data-short-title")||"Gameplay Short",'
        'gu=b.getAttribute("data-game-url"),gt=b.getAttribute("data-game-title");'
        't.textContent=title;y.href="https://www.youtube.com/shorts/"+id;'
        'if(gu){c.href=gu;c.textContent="Play "+(gt||"This Game");c.hidden=false;}else{c.hidden=true;}'
        'p.innerHTML=\'<iframe src="https://www.youtube-nocookie.com/embed/\'+id+\'?autoplay=1&rel=0&playsinline=1" '
        'title="\'+title.replace(/"/g,"")+\'" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>\';'
        'm.classList.add("is-open");});});'
        'x.addEventListener("click",close);'
        'm.addEventListener("click",function(e){if(e.target===m)close();});'
        'document.addEventListener("keydown",function(e){if(e.key==="Escape"&&m.classList.contains("is-open"))close();});'
        '})();</script>'
        '</section>'
        '<!--/ps:yt-shorts-shelf-->'
    )


def main():
    try:
        items = fetch_shorts(limit=12)
    except Exception as e:
        print(f"RSS fetch failed ({e}); keeping existing fallback if present.", file=sys.stderr)
        if os.path.exists(JSON_OUT):
            items = json.load(open(JSON_OUT, encoding="utf-8"))
        else:
            return 1

    if not items:
        print("No Shorts returned from RSS.", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(items)} Shorts to {JSON_OUT}")

    txt = open(INDEX_HTML, encoding="utf-8").read()
    txt = re.sub(r"<!--ps:yt-shorts-shelf-->.*?<!--/ps:yt-shorts-shelf-->\n?", "", txt, flags=re.S)
    shelf = render_shelf(items)
    assert "</main>" in txt, "No </main> in index.html"
    txt = txt.replace("</main>", shelf + "\n</main>", 1)
    with open(INDEX_HTML, "w", encoding="utf-8") as f:
        f.write(txt)
    print(f"Updated {INDEX_HTML} with {len(items)}-card Gameplay Shorts shelf.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
