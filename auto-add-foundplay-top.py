#!/usr/bin/env python3
"""
Daily Foundplay top-performers sync for Pixelsprout.

- Respects foundplay.io/robots.txt: reads ONLY public https://foundplay.io/
  homepage and public /games/<slug> pages (never touches /go/, /browse?, /search?).
- Deduplicates against games-index.json and games/*.html.
- Extracts official third-party game URLs published on public /games/<slug> pages
  (or verified direct domain slugs like *-gg) and verifies live HTTP 200 + og:image.
- Checks X-Frame-Options and Content-Security-Policy (frame-ancestors):
    * Embeddable -> normal iframe game page via site_pages.render_game_page
    * Blocked    -> clean link-out game page with creator credit + removal link
- Caps additions per run (default 5) and updates games-index.json + sitemap.xml.
"""
import argparse
import html as h
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request

import site_pages
import site_shell

ROOT = os.path.dirname(os.path.abspath(__file__))
GAMES_DIR = os.path.join(ROOT, "games")
THUMBS_DIR = os.path.join(ROOT, "assets", "thumbs")
GAMES_INDEX_FILE = os.path.join(ROOT, "games-index.json")
SITEMAP_FILE = os.path.join(ROOT, "sitemap.xml")
TRACKING_FILE = os.path.join(ROOT, "foundplay-top-tracking.json")
DOMAIN = "https://www.playpixelsprout.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

IGNORED_HOSTS = (
    "foundplay.io", "w3.org", "schema.org", "x.com", "twitter.com",
    "google", "cloudflare", "discord.gg", "nextjs", "vercel.com",
    "radix", "lucide", "github.com", "mia-games.com", "youtube.com",
    "youtu.be", "instagram.com", "tiktok.com", "reddit.com", "facebook.com",
)

GENRE_MAP = {
    "puzzle": "Puzzle",
    "arcade": "Arcade",
    "action": "Action",
    "adventure": "Adventure",
    "racing": "Racing",
    "sports": "Sports",
    "horror": "Horror",
    "shooter": "Shooting",
    "shooting": "Shooting",
    "strategy": "Strategy",
    "simulation": "Simulation",
    "rpg": "Adventure",
    "casual": "Casual",
    "clicker": "Clicker",
    "kids": "Kids",
}


def fetch(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()


def norm_title(t):
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def load_tracking():
    if os.path.exists(TRACKING_FILE):
        try:
            return json.load(open(TRACKING_FILE, encoding="utf-8"))
        except Exception:
            pass
    return {"added": [], "skipped": []}


def save_tracking(data):
    with open(TRACKING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def download_thumb(img_url, slug):
    os.makedirs(THUMBS_DIR, exist_ok=True)
    _, hdrs, raw = fetch(img_url, timeout=12)
    if len(raw) < 1000:
        raise ValueError("Thumbnail too small")
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        im.thumbnail((640, 640))
        out_path = os.path.join(THUMBS_DIR, f"{slug}.webp")
        im.save(out_path, "WEBP", quality=82)
        return f"/assets/thumbs/{slug}.webp"
    except Exception:
        ctype = hdrs.get("content-type", "")
        ext = ".png" if "png" in ctype else (".webp" if "webp" in ctype else ".jpg")
        out_path = os.path.join(THUMBS_DIR, f"{slug}{ext}")
        with open(out_path, "wb") as f:
            f.write(raw)
        return f"/assets/thumbs/{slug}{ext}"


def extract_public_game_url(slug, body):
    ext = sorted(set(re.findall(r'https?://[a-zA-Z0-9._-]+(?:/[^\s"\'\\<>)]*)?', body)))
    cleaned = []
    for u in ext:
        u = u.rstrip(".,;:)")
        host = urllib.parse.urlparse(u).netloc.lower()
        if not host or any(bad in host for bad in IGNORED_HOSTS):
            continue
        # Normalize known about/subpages to root playable URL
        if host.endswith("plasticbag.party"):
            u = "https://plasticbag.party/"
        cleaned.append(u)
    if cleaned:
        return cleaned[0]
    # Direct domain-like slug fallback (e.g. raceday-gg -> https://raceday.gg/)
    if re.search(r"-(?:gg|io|app|fun|online|xyz)$", slug):
        base, tld = slug.rsplit("-", 1)
        if base and "." not in base:
            return f"https://{base}.{tld}/"
    return ""


def map_genre(raw_genre):
    if isinstance(raw_genre, list):
        raw_genre = raw_genre[0] if raw_genre else "Arcade"
    return GENRE_MAP.get((raw_genre or "").strip().lower(), "Arcade")


def update_games_index(slug, title, category, thumb_path, linkout):
    games_list = json.load(open(GAMES_INDEX_FILE, encoding="utf-8"))
    if any(g.get("slug") == slug for g in games_list):
        return
    entry = {
        "slug": slug,
        "title": title,
        "category": category,
        "thumbnail": thumb_path,
        "source": "foundplay-top",
    }
    if linkout:
        entry["linkout"] = True
    games_list.append(entry)
    with open(GAMES_INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(games_list, f)


def add_to_sitemap(slug):
    sm = open(SITEMAP_FILE, encoding="utf-8").read()
    loc = f"{DOMAIN}/games/{slug}.html"
    if f"<loc>{loc}</loc>" in sm:
        return
    sm = sm.replace("</urlset>", f"  <url><loc>{loc}</loc><priority>0.7</priority></url>\n</urlset>", 1)
    with open(SITEMAP_FILE, "w", encoding="utf-8") as f:
        f.write(sm)


def build_page(slug, title, category, desc, play_url, thumb_path, creator_name, creator_url, linkout):
    host = urllib.parse.urlparse(play_url).netloc or play_url
    credit_html = ""
    if creator_name:
        if creator_url:
            credit_html = f'Game by <a href="{h.escape(creator_url, quote=True)}" target="_blank" rel="noopener">{h.escape(creator_name)}</a>. '
        else:
            credit_html = f"Game by {h.escape(creator_name)}. "
    removal_html = (
        f'<a href="mailto:reachharshyadav@gmail.com?subject={urllib.parse.quote("Removal request: " + title)}">'
        f"Request removal</a>: are you the creator and want this page taken down? Email us and we will remove it."
    )
    if linkout:
        full_desc_html = (
            f"{h.escape(desc)}<br>"
            f'{credit_html}It is played on the creator&rsquo;s site, '
            f'<a href="{h.escape(play_url, quote=True)}" target="_blank" rel="noopener">{h.escape(host)}</a>; '
            f"Pixelsprout only links to it. All rights belong to the creator.<br>{removal_html}"
        )
    else:
        full_desc_html = (
            f"{h.escape(desc)}<br>"
            f'{credit_html}Hosted by <a href="{h.escape(play_url, quote=True)}" target="_blank" rel="noopener">{h.escape(host)}</a>. '
            f"All rights belong to the creator.<br>{removal_html}"
        )

    update_games_index(slug, title, category, thumb_path, linkout)
    catalog = site_shell.load_catalog()
    game_dict = {
        "slug": slug,
        "title": title,
        "category": category,
        "embed_url": play_url,
        "thumbnail": f"{DOMAIN}{thumb_path}",
        "description": desc,
        "orientation": "landscape",
    }
    fields = site_pages.new_game_fields(game_dict)
    fields["description_html"] = full_desc_html
    html_out = site_pages.render_game_page(fields, catalog, total_games=len(catalog))

    if linkout:
        html_out = html_out.replace('<body class="game-page"', '<body class="game-page" data-linkout="1"', 1)
        linkout_stage = (
            f'<div class="stage"><div class="linkout" style="display:flex;flex-direction:column;align-items:center;'
            f'justify-content:center;gap:14px;min-height:260px;padding:24px;text-align:center;border-radius:14px;'
            f'background:var(--surface,#121712)">'
            f'<img src="{h.escape(thumb_path, quote=True)}" alt="{h.escape(title, quote=True)}" width="400" height="225" '
            f'style="max-width:100%;height:auto;border-radius:12px">'
            f'<a class="btn-lime" href="{h.escape(play_url, quote=True)}" target="_blank" rel="noopener" '
            f'style="display:inline-flex;min-height:52px;align-items:center;padding:0 24px;font-size:1.1rem">'
            f'Play on {h.escape(host)} (opens their site)</a>'
            f'<small>This game runs on the creator&rsquo;s own site and can&rsquo;t be embedded here.</small>'
            f'</div></div>'
        )
        html_out = re.sub(r'<div class="stage">.*?</div></div>', linkout_stage, html_out, count=1, flags=re.S)

    out_path = os.path.join(GAMES_DIR, f"{slug}.html")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html_out)
    add_to_sitemap(slug)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()

    tracking = load_tracking()
    catalog = json.load(open(GAMES_INDEX_FILE, encoding="utf-8"))
    existing_slugs = {g.get("slug") for g in catalog}
    existing_titles = {norm_title(g.get("title")) for g in catalog}

    _, _, home_raw = fetch("https://foundplay.io/", timeout=15)
    home = home_raw.decode("utf-8", errors="ignore")
    slugs = []
    for m in re.finditer(r'href="/games/([a-z0-9-]+)"', home):
        s = m.group(1)
        if s not in slugs:
            slugs.append(s)

    added = []
    for slug in slugs:
        if len(added) >= args.limit:
            break
        if slug in existing_slugs or os.path.exists(os.path.join(GAMES_DIR, f"{slug}.html")):
            continue
        if slug in tracking["added"]:
            continue
        try:
            _, _, p_raw = fetch(f"https://foundplay.io/games/{slug}", timeout=12)
            body = p_raw.decode("utf-8", errors="ignore")
            lds = re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
            vg = json.loads(lds[0]) if lds else {}
            title = (vg.get("name") or slug.replace("-", " ").title()).strip()
            if norm_title(title) in existing_titles:
                continue
            desc = (vg.get("description") or f"Play {title} free in your browser.").strip()
            category = map_genre(vg.get("genre"))
            author = vg.get("author") if isinstance(vg.get("author"), dict) else {}
            creator_name = (author.get("name") or "").strip()
            creator_url = (author.get("url") or "").strip()

            play_url = extract_public_game_url(slug, body)
            if not play_url:
                continue

            code, hdrs, g_raw = fetch(play_url, timeout=10)
            if code != 200:
                continue
            gbody = g_raw.decode("utf-8", errors="ignore")
            ogimg = re.findall(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', gbody, re.I)
            if not ogimg:
                ogimg = re.findall(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', gbody, re.I)
            if not ogimg:
                continue
            img_url = urllib.parse.urljoin(play_url, ogimg[0])
            thumb_path = download_thumb(img_url, slug)

            xfo = hdrs.get("x-frame-options", "")
            csp = hdrs.get("content-security-policy", "")
            linkout = bool(re.search(r"deny|sameorigin", xfo, re.I)) or bool(
                re.search(r"frame-ancestors\s+(?:'none'|'self')", csp, re.I)
            )

            build_page(slug, title, category, desc, play_url, thumb_path, creator_name, creator_url, linkout)
            tracking["added"].append(slug)
            existing_slugs.add(slug)
            existing_titles.add(norm_title(title))
            added.append((slug, title, "link-out" if linkout else "iframe", play_url))
            print(f"ADDED | {'LINKOUT' if linkout else 'IFRAME ':7s} | {slug} -> {play_url}")
        except Exception as e:
            print(f"SKIP  | {slug}: {e}")

    save_tracking(tracking)
    print(f"Foundplay top-performers sync complete: added {len(added)} games.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
