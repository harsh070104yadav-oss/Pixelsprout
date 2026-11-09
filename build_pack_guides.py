#!/usr/bin/env python3
"""One-off: render the 6 AI-build-pack guide pages (2026-11-10) into /guides/<slug>/index.html with the
normal guide layout (site_pages.render_guide_page). Copy fixes keep visible FAQ and FAQPage JSON-LD identical."""
import html, json, os, re, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import site_pages as P
import site_shell as S

PACK = sys.argv[1] if len(sys.argv) > 1 else "/workspace/pixelsprout/ai-build-pack-2026-11-10/pages"
SLUGS = ["sites-like-poki-crazygames", "games-like-getting-over-it-online-free", "free-online-puzzle-games-no-download",
         "browser-games-chromebook", "free-horror-games-browser-no-download", "games-like-brawl-stars-browser"]
TOUCH = "They are built for mouse and keyboard on desktop and have not been tested on touch screens."
# Copy fixes (no mobile/compatibility claims we have not tested; nothing we cannot back up).
FIXES = [
    ("They are built mainly for desktop with mouse and keyboard. Touch play has not been tested for every title.", TOUCH),
    ("They are built mainly for desktop. Some work with touch, but touch play has not been tested for every title.", TOUCH),
    ("They are built for mouse and keyboard on desktop. Touch play has not been tested for every title.", TOUCH),
    ("Games are built for mouse and keyboard on desktop. Some may work on phones, but touch play has not been tested for every title.",
     "Games are built for mouse and keyboard on desktop and have not been tested on touch screens."),
    ("Games are built for mouse and keyboard on desktop, and touch play has not been tested for every title.",
     "Games are built for mouse and keyboard on desktop and have not been tested on touch screens."),
    ("The official Brawl Stars by Supercell is a mobile game for iOS and Android and has no official browser version that we know of. Pixelsprout does not host Supercell's Brawl Stars.",
     "Pixelsprout does not host Supercell's Brawl Stars, and we don't know of an official browser version."),
    ("and a few, like 2048, Snake and Memory Match, are Pixelsprout originals.",
     'and a few, listed in the <a href="/originals/">Originals</a> section, are Pixelsprout originals.'),
    ("Not the official game. Brawl Stars by Supercell is a mobile game for iOS and Android. ", "Not the official game. "),
    ("Online .io games pair you with real people when servers are busy, so results vary from match to match.",
     "Some .io games match you with other online players, so results vary from match to match."),
    ("Yes. Pixelsprout games are HTML5 and run in the Chrome browser on Chromebooks, with no download or install, as long as your network allows the site.",
     "Usually, yes. Pixelsprout games are HTML5 and run in the Chrome browser with no download or install, as long as your network allows the site. We have not tested every game on every Chromebook model."),
    ("Most HTML5 games run in Chrome on a Chromebook if your network allows the site. Some games need a mouse or keyboard.",
     "Most HTML5 games run in Chrome on a Chromebook if your network allows the site, but we have not tested every game on every model. Some games need a mouse or keyboard."),
]
DOMAIN_WWW = "https://www.playpixelsprout.com"


def fix(s, is_json=False):
    for a, b in FIXES:
        if is_json:
            a, b = json.dumps(a)[1:-1], json.dumps(b)[1:-1]
        s = s.replace(a, b)
        s = s.replace(html.escape(a, quote=False), html.escape(b, quote=False))
        s = s.replace(a.replace("'", "&#x27;"), b.replace("'", "&#x27;"))
    return s


def faq_pairs_visible(body):
    faq = body.split("<h2>FAQ</h2>", 1)[1]
    return [(html.unescape(re.sub(r"<[^>]+>", "", q)).strip(), html.unescape(re.sub(r"<[^>]+>", "", a)).strip())
            for q, a in re.findall(r"<h3>(.*?)</h3>\s*<p>(.*?)</p>", faq, re.S)]


def build(slug):
    src = open(os.path.join(PACK, slug + ".html"), encoding="utf-8").read()
    head, body = src.split("<!-- BODY", 1)
    body = body.split("-->", 1)[1]
    first = lambda pat, s: (re.search(pat, s, re.S) or [None, ""])[1]
    jsonld = [json.dumps(json.loads(fix(b, True))) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', head, re.S)]
    og = "\n".join(m for m in re.findall(r'<meta property="og:[^>]*>', head))
    h1 = first(r"<h1>(.*?)</h1>", body).strip()
    inner = first(r'<article class="guide">(.*?)</article>', body).strip()
    inner = re.sub(r"<h1>.*?</h1>\s*", "", inner, count=1, flags=re.S)
    inner = inner.replace(f'href="{DOMAIN_WWW}/', 'href="/')
    inner = fix(inner).strip()
    f = {
        "title_tag": first(r"<title>(.*?)</title>", head).strip(),
        "meta_desc": first(r'<meta name="description" content="([^"]*)"', head),
        "canonical": first(r'<link rel="canonical" href="([^"]*)"', head),
        "og": og, "jsonld": jsonld,
        "crumbs": [("Home", "/"), ("Guides", "/guides/"), (h1, None)],
        "h1": h1, "eyebrow": "Guide", "game_slug": "", "game_title": "", "genre": "", "thumbnail": "",
        "body": inner, "siblings": [], "related": [],
    }
    # visible FAQ must equal FAQPage JSON-LD
    ld = [json.loads(b) for b in jsonld]
    faq_ld = next(d for d in ld if d.get("@type") == "FAQPage")
    pairs_ld = [(q["name"], q["acceptedAnswer"]["text"]) for q in faq_ld["mainEntity"]]
    vis = faq_pairs_visible(inner)
    assert vis == pairs_ld, (slug, [(a, b) for a, b in zip(vis, pairs_ld) if a != b])
    catalog = S.load_catalog()
    out = P.render_guide_page(f, {g["slug"]: g for g in catalog}, total_games=len(catalog))
    path = os.path.join(ROOT, "guides", slug, "index.html")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8", newline="\n").write(out)
    print("wrote", path.replace(ROOT, ""))


if __name__ == "__main__":
    for s in SLUGS:
        build(s)
