"""Page renderers and extractors built on site_shell.

render_* functions produce complete HTML documents. extract_* functions read
an existing page (old template or current template) back into the same
fields, which is what lets migrate-ui.py re-run safely over pages it has
already converted.
"""

import datetime
import json
import os
import re
import sys

import site_shell as S
from site_shell import esc, text

DOMAIN = S.DOMAIN


def _first(pattern, source, flags=re.DOTALL, default=""):
    m = re.search(pattern, source, flags)
    return m.group(1) if m else default


def _region(name, source):
    return _first(rf"<!--ps:{name}-->(.*?)<!--/ps:{name}-->", source)


def _head_part(html_doc):
    return html_doc.split("</head>", 1)[0]


def _jsonld_blocks(html_doc):
    return re.findall(r'<script type="application/ld\+json">(.*?)</script>', _head_part(html_doc), re.DOTALL)


def _og_meta(html_doc):
    head = _head_part(html_doc)
    tags = re.findall(r'<meta (?:property|name)="(?:og|twitter):[^"]+" content="[^"]*"\s*/?>', head)
    return "\n".join(tags)


def _body_attr(html_doc, name):
    body = _first(r"<body([^>]*)>", html_doc)
    return _first(rf'\s{re.escape(name)}="([^"]*)"', body)


def fix_jsonld_urls(block):
    """Old game pages carry Windows-path URLs like /games/games\\\\slug.html."""
    return re.sub(r"/games/games\\\\+", "/games/", block)


# ---------------------------------------------------------------------------
# Similar games
# ---------------------------------------------------------------------------

_groups_cache = {}


def _catalog_groups(catalog):
    key = id(catalog)
    if key not in _groups_cache:
        groups = {}
        for g in catalog:
            groups.setdefault(S.primary_category(g.get("category", "")), []).append(g)
        _groups_cache.clear()
        _groups_cache[key] = groups
    return _groups_cache[key]


def _curated_similar():
    """Hand-picked lists from content-engine/similar_games.py (CURATED_SIMILAR)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "content-engine", "similar_games.py")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_ps_similar_games", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "CURATED_SIMILAR", {})
    except (OSError, ImportError, SyntaxError):
        return {}


def _similar_module():
    """content-engine/similar_games.py, loaded once."""
    global _SIM_MOD
    if _SIM_MOD is None:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "content-engine", "similar_games.py")
        spec = importlib.util.spec_from_file_location("_ps_similar_games", path)
        _SIM_MOD = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_SIM_MOD)
    return _SIM_MOD


_SIM_MOD = None


def similar_games(slug, category, catalog, count=18):
    """Category + mechanic picks; see similar_games.pick_similar."""
    return _similar_module().pick_similar(slug, category, catalog, count,
                                          primary_category=S.primary_category, stable_hash=S.stable_hash)


# ---------------------------------------------------------------------------
# Game pages
# ---------------------------------------------------------------------------

def orientation_lock_js(orientation):
    orientation = (orientation or "").lower()
    if orientation == "portrait":
        return ("if (screen.orientation && screen.orientation.lock) { "
                "screen.orientation.lock('portrait-primary').catch(() => {}); }")
    if orientation == "landscape":
        return ("if (screen.orientation && screen.orientation.lock) { "
                "screen.orientation.lock('landscape').catch(() => {}); }")
    return ""


def fullscreen_onclick(lock_js=""):
    code = (
        "\n      const f = document.querySelector('.embed-frame-wrap');"
        "\n      const req = f.requestFullscreen || f.webkitRequestFullscreen || f.msRequestFullscreen;"
        "\n      if (req) req.call(f);"
        "\n      if (typeof gtag === 'function') { gtag('event', 'fullscreen_click', { game_title: document.title }); }"
    )
    if lock_js:
        code += "\n      " + lock_js
    return code + "\n    "


def game_jsonld(game):
    schema = {
        "@context": "https://schema.org",
        "@type": "VideoGame",
        "name": game["title"],
        "description": game.get("description", ""),
        "genre": game["category"],
        "url": f"{DOMAIN}/games/{game['slug']}.html",
        "applicationCategory": "Game",
        "operatingSystem": "Web Browser",
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
    }
    if game.get("thumbnail"):
        schema["image"] = game["thumbnail"]
    crumb = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{DOMAIN}/"},
            {"@type": "ListItem", "position": 2, "name": game["category"], "item": f"{DOMAIN}/games/{game['slug']}.html"},
            {"@type": "ListItem", "position": 3, "name": game["title"], "item": f"{DOMAIN}/games/{game['slug']}.html"},
        ],
    }
    return [json.dumps(schema), json.dumps(crumb)]


def new_game_fields(game):
    """Fields for a brand-new game coming from a provider feed."""
    title = game["title"]
    desc_text = text(game.get("description", ""))
    return {
        "slug": game["slug"],
        "title": title,
        "title_tag": f"{esc(title)} — Pixelsprout",
        "meta_desc": esc(f"Play {title} free online, no download needed."),
        "category": game["category"],
        "embed_url": EMBED_URL_OVERRIDE.get(game["slug"], game["embed_url"]),
        "thumbnail": game.get("thumbnail", ""),
        "description_html": esc(desc_text),
        "onclick": fullscreen_onclick(orientation_lock_js(game.get("orientation"))),
        "jsonld": game_jsonld(game),
    }


def extract_game_page(doc, slug):
    """Returns the fields of an iframe game page, or None for built-in games."""
    embed = _first(r'<iframe src="([^"]*)"', doc)
    if not embed:
        return None
    title_html = _first(r'<h1 class="(?:game-title|game-bar__title)">(.*?)</h1>', doc)
    if _body_attr(doc, "data-page") == "game":
        category = text(_body_attr(doc, "data-genre"))
        thumbnail = text(_body_attr(doc, "data-thumbnail"))
        description = _region("desc", doc)
        data_title = text(_body_attr(doc, "data-title"))
    else:
        category = text(_first(r'<div class="game-meta">(.*?)</div>', doc))
        thumbnail = text(_first(r'data-thumbnail="([^"]*)"', doc))
        description = _first(r'<p class="how-to">(.*?)</p>', doc).strip()
        data_title = ""
    onclick = _first(r'fullscreen-btn"[^>]*?onclick="([^"]*)"', doc)
    fields = {
        "slug": slug,
        "title": data_title or S.clean_title(text(title_html)),
        "title_tag": _first(r"<title>(.*?)</title>", doc).strip(),
        "meta_desc": _first(r'<meta name="description" content="([^"]*)"', doc),
        "category": category or "Arcade",
        "embed_url": EMBED_URL_OVERRIDE.get(slug, embed),
        "thumbnail": thumbnail,
        "description_html": description,
        "onclick": onclick or fullscreen_onclick(),
        "jsonld": [fix_jsonld_urls(b) for b in _jsonld_blocks(doc)],
    }
    if not fields["thumbnail"]:
        for block in fields["jsonld"]:
            try:
                img = json.loads(block).get("image")
            except (ValueError, AttributeError):
                img = None
            if img:
                fields["thumbnail"] = img
                break
    return fields


GUIDE_LABELS = [
    ("tips", "Tips & strategy", "lightbulb"),
    ("controls", "Controls", "keyboard"),
    ("beginner-guide", "Beginner's guide", "flag-start"),
    ("faq", "FAQ", "help"),
    ("similar", "Games like this", "layers"),
]


FAN_MADE_SLUGS = {"getting-over-it", "baba-is-you"}
FAN_MADE_CREDIT = {"getting-over-it": "griffpatch"}

# Trademark-style notices shown directly above the game embed (visible without scrolling).
_BRAWL_NOTICE = ('<strong>Not the official Brawl Stars.</strong> This is an unofficial browser arena game from a third-party '
                 'publisher. It is not made by or affiliated with Supercell. Looking for similar games? See '
                 '<a href="/guides/games-like-brawl-stars-browser/">games like Brawl Stars you can play in your browser</a>.')
_AMONG_NOTICE = ('<strong>Not the official Among Us.</strong> This is an unofficial browser game from a third-party '
                 'publisher. It is not made by or affiliated with InnerSloth. Looking for similar games? See '
                 '<a href="/guides/games-like-among-us-online-free/">games like Among Us you can play free online</a>.')
_FIREBOY_NOTICE = ('<strong>Not the official Fireboy and Watergirl games.</strong> This is an unofficial browser '
                   'co-op game from a third-party publisher. It is not the original Forest Temple series. See '
                   '<a href="/guides/two-player-games-like-fireboy-and-watergirl/">two-player games like Fireboy and Watergirl</a>.')
GAME_NOTICES = {
    "brawl-stars": _BRAWL_NOTICE,
    "brawl-stars-2": _BRAWL_NOTICE,
    "among-us": _AMONG_NOTICE,
    "fireboy-and-watergirl-online": _FIREBOY_NOTICE,
}

# Extra visible FAQ shown on the game page (static HTML). add-faq-jsonld.py appends the same pairs to the
# page's FAQPage JSON-LD after the 4 "At a glance" questions, so the schema only carries visible text.
GAME_FAQ_EXTRA = {
    "brawl-stars": [
        ("Is this the official Brawl Stars by Supercell?",
         "No. This is an unofficial arena game from a third-party publisher that uses the name Brawl Stars. "
         "It is not made by or affiliated with Supercell."),
        ("How do you play?",
         "Pick a hero, move around the arena, eat food items to evolve and grow stronger, use upgrades for "
         "speed and health, and battle opponents."),
        ("What other browser games are like Brawl Stars?",
         "Try Brawlers Arena Battle Stars, Ace Brawl Battle 3D, Archers.io, Guardz IO, Sword Battle.io and Diep.io."),
    ],
}


# Answer-first ChatGPT landing pages (10 Nov 2026). H1 + lead answer "play X free online, no download".
# Visible FAQ (h3) must match FAQPage JSON-LD for these slugs; dateModified is ISO date.
ANSWER_FIRST = {
    "brawl-stars": {
        "h1": "Play Brawl Stars Free Online — No Download",
        "title_tag": "Play Brawl Stars Free Online (Unofficial, No Download) | Pixelsprout",
        "meta_desc": "Play an unofficial Brawl Stars browser arena free online on Pixelsprout. No download, no sign-up. Not made by or affiliated with Supercell.",
        "lead": "Play Brawl Stars free online in your browser on Pixelsprout — no download and no sign-up. This is an unofficial .io-style arena game from a third-party publisher (not Supercell): pick a hero, move around the arena, eat food to evolve, use speed and health upgrades, and battle other players. Built for mouse and keyboard; touch play has not been tested on this page.",
        "faq": [
            ("Can I play Brawl Stars free online with no download?",
             "Yes. Open this page and the unofficial browser arena loads in the tab. There is nothing to install and no Pixelsprout account required."),
            ("Is this the official Brawl Stars by Supercell?",
             "No. This is an unofficial arena game from a third-party publisher that uses the name Brawl Stars. It is not made by or affiliated with Supercell."),
            ("How do you play?",
             "Pick a hero, move around the arena, eat food items to evolve and grow stronger, use upgrades for speed and health, and battle opponents."),
            ("What similar games can I play next?",
             "Try Brawlers Arena Battle Stars, Ace Brawl Battle 3D and Archers.io — all free in the browser on Pixelsprout."),
        ],
        "similar": ["brawlers-arena-battle-stars", "ace-brawl-battle-3d", "archers-io"],
        "date_modified": "2026-11-10",
    },
    "getting-over-it": {
        "h1": "Play Getting Over It Free Online — No Download",
        "title_tag": "Play Getting Over It Free Online (No Download) | Pixelsprout",
        "meta_desc": "Play Getting Over It free online in your browser on Pixelsprout. Fan-made Scratch remake, no download. Climb with the mouse and hammer.",
        "lead": "Play Getting Over It free online in your browser on Pixelsprout — no download and no sign-up. This page hosts a fan-made Scratch remake by griffpatch (not the original Bennet Foddy game): you guide a cat in a pot up a pile of giant Scratch blocks using a hammer controlled with the mouse. The first load can take a few minutes. Built for mouse play; this version has no on-screen touch controls.",
        "faq": [
            ("Can I play Getting Over It free online with no download?",
             "Yes. The Scratch remake loads in this page. There is nothing to install and no account required."),
            ("Is this the original Getting Over It?",
             "No. This is a fan-made Scratch remake by griffpatch, not the original game by Bennett Foddy."),
            ("How do you control it?",
             "Use the mouse only. The hammer head follows your cursor; plant it on a block and pull the opposite way to push the pot up or along."),
            ("What similar games can I play next?",
             "Try Mad Climbing, Carrot Climber and Stickman That One Level — climbing challenges you can open free in the browser."),
        ],
        "similar": ["mad-climbing", "carrot-climber", "stickman-that-one-level"],
        "date_modified": "2026-11-10",
    },
    "your-dream-room": {
        "h1": "Play Your Dream Room Free Online — No Download",
        "title_tag": "Play Your Dream Room Free Online (No Download) | Pixelsprout",
        "meta_desc": "Play Your Dream Room free online in your browser on Pixelsprout. Design rooms with wallpaper, flooring and furniture. No download.",
        "lead": "Play Your Dream Room free online in your browser on Pixelsprout — no download and no sign-up. It is a room-design game: pick wallpaper, flooring, furniture and accessories to build cozy interiors. Use the mouse and keyboard as the on-screen prompts show when the game loads. Built for mouse and keyboard; touch play has not been tested on this page.",
        "faq": [
            ("Can I play Your Dream Room free online with no download?",
             "Yes. The game loads in this browser tab. Nothing to install and no account required."),
            ("What do you do in the game?",
             "Decorate empty spaces with wallpaper, flooring, furniture and accessories until the room feels finished."),
            ("How do you control it?",
             "Use your mouse and keyboard following the in-game prompts after it loads."),
            ("What similar games can I play next?",
             "Try Girl Game Organizing Fun, Big Supermarket Simulator and Amanda's Perfect Date — free browser games on Pixelsprout."),
        ],
        "similar": ["girl-game-organizing-fun", "big-supermarket-simulator", "amandas-perfect-date"],
        "date_modified": "2026-11-10",
    },
    "amandas-perfect-date": {
        "h1": "Play Amanda's Perfect Date Free Online — No Download",
        "title_tag": "Play Amanda's Perfect Date Free Online (No Download) | Pixelsprout",
        "meta_desc": "Play Amanda's Perfect Date free online in your browser on Pixelsprout. Dress-up dating game, no download, no sign-up.",
        "lead": "Play Amanda's Perfect Date free online in your browser on Pixelsprout — no download and no sign-up. Help Amanda get ready for a date: choose her outfit, hairstyle and date, then see how the night goes. Click the options on screen to pick looks and progress. Built for mouse and keyboard; touch play has not been tested on this page.",
        "faq": [
            ("Can I play Amanda's Perfect Date free online with no download?",
             "Yes. Open this page and the dress-up game loads in the tab. No install and no account required."),
            ("What kind of game is it?",
             "A girls' fashion and dating dress-up game: style Amanda, pick her date and check the compatibility score."),
            ("How do you play?",
             "Click through the on-screen choices for outfits, hairstyles and her date partner, then see the result."),
            ("What similar games can I play next?",
             "Try Your Dream Room, Girl Game Organizing Fun and Highschool Mean Girls 2 — free in the browser on Pixelsprout."),
        ],
        "similar": ["your-dream-room", "girl-game-organizing-fun", "highschool-mean-girls-2"],
        "date_modified": "2026-11-10",
    },
    "dont-splash": {
        "h1": "Play Don't Splash Free Online — No Download",
        "title_tag": "Play Don't Splash Free Online (No Download) | Pixelsprout",
        "meta_desc": "Play Don't Splash free online in your browser on Pixelsprout. 3D horror platformer in an abandoned pool. No download.",
        "lead": "Play Don't Splash free online in your browser on Pixelsprout — no download and no sign-up. It is a 3D horror platformer set in an abandoned pool: jump from tile to tile through the fog, avoid falling in (a blind creature hunts by sound), and cross rooms of sinking tiles and traps. Use the on-screen controls in the game frame (mouse and keyboard). Built for mouse and keyboard; touch play has not been tested on this page.",
        "faq": [
            ("Can I play Don't Splash free online with no download?",
             "Yes. The game loads in this browser tab. Nothing to install and no account required."),
            ("What is Don't Splash?",
             "A 3D horror platformer in an endless abandoned pool. Stay out of the water, cross nine rooms of hazards, and use thrown objects to lure the monster away."),
            ("How do you play?",
             "Move and jump across tiles in the game frame using the on-screen control prompts. Avoid splashing; one fall alerts the creature."),
            ("What similar games can I play next?",
             "Try The Man From The Window, Boo Scared? Escape from Backrooms and Granny House Escape — free browser horror on Pixelsprout."),
        ],
        "similar": ["the-man-from-the-window", "boo-scared-escape-from-backrooms", "granny-house-escape"],
        "date_modified": "2026-11-10",
    },
    "among-us": {
        "h1": "Play Among Us Free Online — No Download",
        "title_tag": "Play Among Us Free Online (Unofficial, No Download) | Pixelsprout",
        "meta_desc": "Play an unofficial Among Us-style browser game free online on Pixelsprout. No download, no sign-up. Not made by or affiliated with InnerSloth.",
        "lead": "Play Among Us free online in your browser on Pixelsprout — no download and no sign-up. This is an unofficial browser game from a third-party publisher (not InnerSloth): crewmates complete tasks while looking for an impostor, and you talk, vote and try to last until the end. Use the on-screen controls in the game frame (mouse and keyboard). Built for mouse and keyboard; touch play has not been tested on this page.",
        "faq": [
            ("Can I play Among Us free online with no download?",
             "Yes. Open this page and the unofficial browser game loads in the tab. There is nothing to install and no Pixelsprout account required."),
            ("Is this the official Among Us by InnerSloth?",
             "No. This is an unofficial browser game from a third-party publisher that uses the name Among Us. It is not made by or affiliated with InnerSloth."),
            ("How do you play?",
             "Complete tasks if you are a crewmate, or blend in if you are the impostor. Use the on-screen buttons and the discussion and voting screens as they appear."),
            ("What similar games can I play next?",
             "Try Eggdog Among Us, The Dead Among Us and Red Impostor Guys — all free in the browser on Pixelsprout."),
        ],
        "similar": ["eggdog-among-us", "the-dead-among-us", "red-impostor-guys"],
        "date_modified": "2026-11-11",
    },
    "fireboy-and-watergirl-online": {
        "h1": "Play Fireboy and Watergirl Free Online — No Download",
        "title_tag": "Play Fireboy and Watergirl Free Online (Unofficial, No Download) | Pixelsprout",
        "meta_desc": "Play an unofficial Fireboy and Watergirl co-op game free online on Pixelsprout. Two-player temple puzzles, no download. Not the original Forest Temple games.",
        "lead": "Play Fireboy and Watergirl free online in your browser on Pixelsprout — no download and no sign-up. This is an unofficial two-player co-op game from a third-party publisher (not the original Forest Temple series): guide Fireboy and Watergirl through temple rooms, collect gems and solve puzzles together. One player can use the keyboard for each character, or two people can share the same keyboard, following the on-screen control prompts. Built for mouse and keyboard; touch play has not been tested on this page.",
        "faq": [
            ("Can I play Fireboy and Watergirl free online with no download?",
             "Yes. The unofficial browser game loads in this tab. Nothing to install and no account required."),
            ("Is this the original Fireboy and Watergirl?",
             "No. This is an unofficial co-op game from a third-party publisher. It is not the original Forest Temple series."),
            ("Can two people play on one keyboard?",
             "Yes. It is built as a two-player game. Follow the on-screen control prompts for each character. Built for mouse and keyboard; touch play has not been tested on this page."),
            ("What similar games can I play next?",
             "Try 12 MiniBattles - Two Players, Steve Alex Spooky - 2 Player and Nooby And Obby 2 Player — free two-player browser games on Pixelsprout."),
        ],
        "similar": ["12-minibattles-two-players", "steve-alex-spooky-2-player", "nooby-and-obby-2-player"],
        "date_modified": "2026-11-11",
    },
    "referee-simulator-3d": {
        "h1": "Play Referee Simulator 3D Free Online — No Download",
        "title_tag": "Play Referee Simulator 3D Free Online (No Download) | Pixelsprout",
        "meta_desc": "Play Referee Simulator 3D free online in your browser on Pixelsprout. Call fouls, penalties and VAR reviews. No download, no sign-up.",
        "lead": "Play Referee Simulator 3D free online in your browser on Pixelsprout — no download and no sign-up. You take the whistle in a 3D sports match: stay with play, call fouls, penalties, offsides and cards, and use the pitchside VAR monitor when you are unsure. Use the on-screen controls in the game frame (mouse and keyboard). Built for mouse and keyboard; touch play has not been tested on this page.",
        "faq": [
            ("Can I play Referee Simulator 3D free online with no download?",
             "Yes. The game loads in this browser tab. Nothing to install and no account required."),
            ("What do you do in the game?",
             "You referee a match in 3D: run with play, decide fouls, penalties, offsides and cards, and review incidents on the VAR monitor when needed."),
            ("How do you control it?",
             "Use the mouse and keyboard following the on-screen prompts in the game frame."),
            ("What similar games can I play next?",
             "Try Crazy Goal, Ninja Head Ball and Gully Cricket — free sports games in the browser on Pixelsprout."),
        ],
        "similar": ["crazy-goal", "ninja-head-ball", "gully-cricket"],
        "date_modified": "2026-11-11",
    },
    "scary-doll": {
        "h1": "Play Scary Doll Free Online — No Download",
        "title_tag": "Play Scary Doll Free Online (No Download) | Pixelsprout",
        "meta_desc": "Play Scary Doll free online in your browser on Pixelsprout. Stealth horror in a house with a porcelain doll. No download.",
        "lead": "Play Scary Doll free online in your browser on Pixelsprout — no download and no sign-up. It is a stealth horror game: you are in a decaying house with a porcelain doll, so you explore rooms quietly, solve puzzles and try to escape without making too much noise. Use the on-screen controls in the game frame (mouse and keyboard). Built for mouse and keyboard; touch play has not been tested on this page.",
        "faq": [
            ("Can I play Scary Doll free online with no download?",
             "Yes. The game loads in this browser tab. Nothing to install and no account required."),
            ("What is Scary Doll?",
             "A stealth horror escape: move quietly through a house, solve puzzles and avoid a porcelain doll that comes after you if you make too much noise."),
            ("How do you play?",
             "Explore room by room with the mouse and keyboard, following the on-screen prompts. Stay quiet and look for a way out."),
            ("What similar games can I play next?",
             "Try Granny House Escape, The House 2 and Escape From Work — free browser horror on Pixelsprout."),
        ],
        "similar": ["granny-house-escape", "the-house-2", "escape-from-work"],
        "date_modified": "2026-11-11",
    },
    "renovation": {
        "h1": "Play Renovation! Free Online — No Download",
        "title_tag": "Play Renovation! Free Online (No Download) | Pixelsprout",
        "meta_desc": "Play Renovation! free online in your browser on Pixelsprout. Move furniture, sell houses and rebuild. No download, no sign-up.",
        "lead": "Play Renovation! free online in your browser on Pixelsprout — no download and no sign-up. You control workers who move furniture out of messy houses, sell the homes and spend the money to build new ones. Use the on-screen controls in the game frame (mouse and keyboard). Built for mouse and keyboard; touch play has not been tested on this page.",
        "faq": [
            ("Can I play Renovation! free online with no download?",
             "Yes. The game loads in this browser tab. Nothing to install and no account required."),
            ("What do you do in the game?",
             "Direct workers to clear furniture from messy houses, sell those houses and use the money to build new ones."),
            ("How do you control it?",
             "Use the mouse and keyboard following the on-screen prompts after the game loads."),
            ("What similar games can I play next?",
             "Try Your Dream Room, Big Supermarket Simulator and Girl Game Organizing Fun — free in the browser on Pixelsprout."),
        ],
        "similar": ["your-dream-room", "big-supermarket-simulator", "girl-game-organizing-fun"],
        "date_modified": "2026-11-11",
    },
}

# One-line notices shown directly above the game embed (same rd-note style as the notes in descriptions).
# Short-video landing pages (YouTube Shorts / Instagram traffic). Only these slugs get the
# eager, high-priority embed, the "Play now" button and the 3 picked games right under the game.
LANDING_PAGES = {
    "books-tower": {"related": ["tower-blocks-deluxe-3d", "stack-block-up", "buildtower"]},
    "protect-my-dog": {"related": ["save-the-doge", "draw-save-puzzle", "stickman-rescue-draw-2-save"]},
}


# Phones: drop the breadcrumb row (the JSON-LD breadcrumb stays) so the game starts higher.
LANDING_HEAD = ('<link rel="preconnect" href="https://play.gamepix.com" crossorigin>\n'
                '<style>@media (max-width:767px){.game-main>.crumbs{display:none}'
                '.game-main>.game-bar{padding:var(--sp-2) 0}}</style>\n')
# Hide crumbs on small screens so the playable frame sits higher (all game pages).
GAME_MOBILE_CSS = ('<style>.game-page .rd-note,.game-page .game-notice,.game-page .game-bar__title,.game-page .how-to,.game-page .similar-inline,.game-page .last-updated{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}@media (max-width:767px){.game-main>.crumbs{display:none}.game-main>.rd-note{margin:var(--sp-2) 0}}</style>\n')



def _landing_related(slug, catalog):
    by_slug = {g["slug"]: g for g in catalog}
    games = [by_slug[s] for s in LANDING_PAGES[slug]["related"] if s in by_slug]
    if not games:
        return ""
    cards = "".join(S.card(g) for g in games)
    return (f'<section class="landing-related" aria-labelledby="landing-related-title" style="margin-top:var(--sp-4)">'
            f'<h2 class="block__title" id="landing-related-title">Play next</h2>'
            f'<div class="shelf__track" style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:var(--sp-3)">{cards}</div></section>\n')


EMBED_NOTES = {
    "spellwright": "Crafting spells needs a Discord sign-in on the creator's site.",
    "gigacity": "Needs a browser with WebGPU or WebGL 2.",
    "getting-over-it": "The first load can take a few minutes.",
    "touchthemoon": "Built for touch screens; mouse play didn't work in our test.",
}

# Per-game embed URL override (used instead of the provider embed on re-render) and an optional
# plain source link shown right under the player.
EMBED_URL_OVERRIDE = {
    "getting-over-it": "https://scratch.mit.edu/projects/389464290/embed",
}
EMBED_SOURCE_LINK = {
    "getting-over-it": ("View on Scratch", "https://scratch.mit.edu/projects/389464290/"),
}

# Override for the "Is <game> free to play?" FAQ answer (visible FAQ via data-faq-free, JSON-LD via add-faq-jsonld.py).
FAQ_FREE_ANSWER = {
    "spellwright": "Yes. Spellwright is free on Pixelsprout and plays in your web browser with no download. "
                   "Casting or crafting spells needs a Discord sign-in on the creator's site.",
}


# Override for the "Can I play <game> on my phone?" FAQ answer (visible FAQ via data-faq-mobile, JSON-LD via add-faq-jsonld.py).
# Use it where phone play has not been checked, so the FAQ does not claim it works on mobile.
_MOBILE_UNTESTED = "It's built for desktop browsers; mobile support depends on the game and wasn't tested here."
FAQ_MOBILE_ANSWER = {
    "vibe-theft-auto": _MOBILE_UNTESTED,
    "gigacity": "It's built for desktop browsers and needs a browser with WebGPU or WebGL 2; mobile support wasn't tested here.",
    "tweetcraft": _MOBILE_UNTESTED,
    "hood-warfare": _MOBILE_UNTESTED,
    "poople": _MOBILE_UNTESTED,
    "8compute": _MOBILE_UNTESTED,
    "books-tower": "Not tested on touch screens.",
    "protect-my-dog": "Not tested on touch screens.",
    "brawl-stars": "It's built for desktop with mouse and keyboard. Touch play has not been tested on this page.",
    "getting-over-it": "It's built for mouse play. This version has no on-screen touch controls, and touch play hasn't been tested here.",
}


# Per-game "See it in action" links to Pixelsprout's own gameplay clips, shown on the game page.
GAME_SOCIAL_LINKS = {

    "getting-over-it": [
        ("YouTube Short (Part 1, 10 Nov)", "https://youtube.com/shorts/QP6ngTJL1tk"),
        ("YouTube Short (Part 2, 10 Nov)", "https://youtube.com/shorts/d8bWKiCNZzE"),
        ("YouTube Short (Part 3, 10 Nov)", "https://youtube.com/shorts/XvA67BWEa0E"),
        ("YouTube Short (4'51 at the tower, 10 Nov)", "https://youtube.com/shorts/zFjHUEt0RnE"),
        ("YouTube Short (Part 4, 11 Nov)", "https://youtube.com/shorts/gKptSEmKcI8"),
    ],
    "escape-tsunami-for-brainrots": [
        ("YouTube Short (fast wave, 10 Nov)", "https://youtube.com/shorts/tL4Zhker8wQ"),
        ("YouTube Short (slow wave, 11 Nov)", "https://youtube.com/shorts/lDucmG_wOK8"),
    ],
    "moto-x3m-spooky-land": [
        ("YouTube Short (Part 1)", "https://youtube.com/shorts/qZ1AI9H69gI"),
        ("YouTube Short (Part 2)", "https://youtube.com/shorts/VSXzofGETf0"),
        ("YouTube Short (Part 3)", "https://youtube.com/shorts/CevJXC4bbIU"),
    ],
    "gravity-merge": [
        ("YouTube Short", "https://youtube.com/shorts/Uq54-x44wEQ"),
        ("Post on X", "https://x.com/Playpixelsprout/status/2112692842246099447"),
        ("Post on Instagram", "https://www.instagram.com/playpixelsprout/p/DevDWn8gVus/"),
        ("Reel on Instagram", "https://www.instagram.com/playpixelsprout/reel/DevDYI3nT2F/"),
        ("Reel on Facebook", "https://www.facebook.com/reel/29291042653846498"),
    ],
    "test-tubes-water-sort": [
        ("Post on Instagram", "https://www.instagram.com/playpixelsprout/p/DesejofkWTV/"),
        ("Reel on Instagram", "https://www.instagram.com/playpixelsprout/reel/DesekHglQLq/"),
        ("Reel on Facebook", "https://www.facebook.com/reel/1130420909503778"),
        ("YouTube Short", "https://youtube.com/shorts/D3Xf1b7Xz88"),
        ("Post on X", "https://x.com/Playpixelsprout/status/2112330454678245590"),
    ],
    "star-hop-endless-galaxy": [
        ("Post on Instagram", "https://www.instagram.com/playpixelsprout/p/Dep5xvvCpGI/"),
        ("Reel on Instagram", "https://www.instagram.com/playpixelsprout/reel/Dep5zX3iSSJ/"),
    ],
    "books-tower": [
        ("YouTube Short (Part 1)", "https://youtube.com/shorts/BASeAbjnGPE"),
        ("YouTube Short (Part 2)", "https://youtube.com/shorts/JQV5enwFlSA"),
        ("Reel on Instagram", "https://www.instagram.com/playpixelsprout/reel/De2onZlis0L/"),
        ("YouTube Short (Part 3)", "https://youtube.com/shorts/kJpeplOfVcU"),
        ("YouTube Short (Part 5)", "https://youtube.com/shorts/LLCLi8DCzzU"),
        ("YouTube Short (Part 6)", "https://youtube.com/shorts/OT49uCJYExk"),
        ("Reel on Instagram (Part 1)", "https://www.instagram.com/playpixelsprout/reel/DfGGJh5v-3X/"),
        ("Reel on Instagram (Part 2)", "https://www.instagram.com/playpixelsprout/reel/DfGRB0TvJ_i/"),
        ("Reel on Instagram (Part 3)", "https://www.instagram.com/playpixelsprout/reel/DfIn8oRvg5X/"),
        ("Reel on Instagram (30 Oct Short)", "https://www.instagram.com/playpixelsprout/reel/DfIo2wiPc8D/"),
        ("Reel on Instagram (Part 5)", "https://www.instagram.com/playpixelsprout/reel/DfLRN5Kv3OJ/"),
        ("Reel on Instagram (Part 6)", "https://www.instagram.com/playpixelsprout/reel/DfLVFPvvGY-/"),
        ("YouTube Short (Part 7)", "https://youtube.com/shorts/h3iL_0RqJBI"),
        ("YouTube Short (Part 8)", "https://youtube.com/shorts/Rh4kmgyUV8o"),
        ("YouTube Short (Part 9)", "https://youtube.com/shorts/ih7EmQfzK9Y"),
        ("Reel on Instagram (Part 9)", "https://www.instagram.com/reel/DfYObTKCADw/"),
        ("Reel on Facebook (Part 9)", "https://www.facebook.com/reel/954153614150444/"),
        ("YouTube Short (Part 10)", "https://youtube.com/shorts/gN_fzHxBGic"),
        ("Reel on Instagram (Part 10)", "https://www.instagram.com/playpixelsprout/reel/DfVu0aJDyZx/"),
        ("YouTube Short (Part 11)", "https://youtube.com/shorts/_9eTAwI7BSc"),
        ("Reel on Instagram (Part 11)", "https://www.instagram.com/playpixelsprout/reel/DfVprLiHceO/"),
    ],
    "protect-my-dog": [
        ("YouTube Short (Part 1)", "https://youtube.com/shorts/bBCWh7W_dyg"),
        ("YouTube Short (Part 2)", "https://youtube.com/shorts/9HDQw-4LYb4"),
        ("YouTube Short (Part 3)", "https://youtube.com/shorts/wUPrd7FjjnE"),
        ("YouTube Short (Part 4)", "https://youtube.com/shorts/rug6f92m0qo"),
        ("YouTube Short (Level 2 beat me 10 times)", "https://youtube.com/shorts/pU36rmo32FU"),
        ("Reel on Instagram (Part 1)", "https://www.instagram.com/playpixelsprout/reel/DfGRR95PF1r/"),
        ("Reel on Instagram (Part 2)", "https://www.instagram.com/playpixelsprout/reel/DfGRdtmPqoJ/"),
        ("Reel on Instagram (Part 3)", "https://www.instagram.com/playpixelsprout/reel/DfGTvYrvlqR/"),
        ("Reel on Instagram (Part 4)", "https://www.instagram.com/playpixelsprout/reel/DfIoO5nPq3O/"),
        ("Reel on Instagram (30 Oct Short)", "https://www.instagram.com/playpixelsprout/reel/DfIpD7ev59z/"),
        ("Reel on Instagram (Level 2 beat me 10 times)", "https://www.instagram.com/reel/DfLTR-OvaNT/"),
        ("YouTube Short (Part 5)", "https://youtube.com/shorts/QQOWoZFERlE"),
        ("YouTube Short (Part 11)", "https://youtube.com/shorts/KoRxM37QRQ8"),
        ("Reel on Instagram (Part 11)", "https://www.instagram.com/playpixelsprout/reel/DfVz5N5gMKO/"),
    ],
    "destroy-the-ragdoll-sandbox": [
        ("YouTube Short (Could you survive 3 seconds?)", "https://youtube.com/shorts/zkL45kj9KTo"),
        ("Reel on Instagram (Could you survive 3 seconds?)", "https://www.instagram.com/reel/DfYR2yzEr6p/"),
        ("Reel on Facebook (Could you survive 3 seconds?)", "https://www.facebook.com/reel/1280852680891722"),
        ("YouTube Short ($11 to $25 in 11 seconds)", "https://youtube.com/shorts/GKaJWX0D_XE"),
        ("Reel on Instagram ($11 to $25 in 11 seconds)", "https://www.instagram.com/reel/DfYd0IfGVfj/"),
        ("Reel on Facebook ($11 to $25 in 11 seconds)", "https://www.facebook.com/reel/1148833438138744"),
    ],
    "car-crash-test": [
        ("YouTube Short (POV: you floor it into a wall)", "https://youtube.com/shorts/FkAOHBA_4a0"),
        ("Reel on Instagram (POV: you floor it into a wall)", "https://www.instagram.com/reel/DfYV1l1jyfo/"),
        ("Reel on Facebook (POV: you floor it into a wall)", "https://www.facebook.com/reel/1652858259553769"),
        ("YouTube Short (dead-center)", "https://www.youtube.com/shorts/QetygKeDIME"),
    ],
    "body-drop-3d": [
        ("YouTube Short (How many times did you watch this loop?)", "https://youtube.com/shorts/ig4ZUb0vFNM"),
        ("Reel on Instagram (How many times did you watch this loop?)", "https://www.instagram.com/reel/DfYZ1RFET2k/"),
        ("Reel on Facebook (How many times did you watch this loop?)", "https://www.facebook.com/reel/1391830772763332"),
    ],
    "buba-jump": [
        ("YouTube Short (Part 1)", "https://youtube.com/shorts/I9OXV5JlIc0"),
        ("Reel on Instagram (Part 1)", "https://www.instagram.com/playpixelsprout/reel/DfIomUlPjKF/"),
    ],
    "carrot-climber": [
        ("YouTube Short", "https://youtube.com/shorts/62IERMxfeuQ"),
        ("Reel on Instagram", "https://www.instagram.com/playpixelsprout/reel/De2vjpGkjoA/"),
    ],
    "stickman-that-one-level": [
        ("YouTube Short", "https://youtube.com/shorts/V5bXbFGrbVk"),
        ("Reel on Instagram", "https://www.instagram.com/playpixelsprout/reel/De22aGdjILa/"),
    ],
    "dot-link-puzzle-connect-the-dots": [
        ("YouTube Short", "https://youtube.com/shorts/58vqtwXVsns"),
        ("Reel on Instagram", "https://www.instagram.com/playpixelsprout/reel/De0NBp6grRs/"),
        ("Reel on Facebook", "https://www.facebook.com/reel/28733576349667923"),
        ("Post on X", "https://x.com/Playpixelsprout/status/2113417618178904207"),
    ],
    "bottle-flip-game": [
        ("YouTube Short", "https://www.youtube.com/shorts/wxiM5W8nnr4"),
    ],
    "faily-skater": [
        ("YouTube Short", "https://www.youtube.com/shorts/GB3MW-aZGpA"),
    ],
    "falling-dummy": [
        ("YouTube Short (Part 1 — X-ray)", "https://www.youtube.com/shorts/LWYfe1-UQzc"),
        ("YouTube Short (Part 2 — Keep Crushing)", "https://www.youtube.com/shorts/cLI7AllbZ9M"),
        ("YouTube Short (Part 3 — Rocket cart)", "https://www.youtube.com/shorts/sX4cKxCGaCc"),
    ],
}


def _social_section(slug, title):
    links = GAME_SOCIAL_LINKS.get(slug)
    if not links:
        return ""
    items = "".join(f'<li><a href="{esc(u)}" rel="noopener" target="_blank">{esc(l)}</a></li>' for l, u in links)
    return (f'<section class="game-about" aria-labelledby="in-action-title"><!--ps:shots-->'
            f'<h2 id="in-action-title">See it in action</h2><p>Watch {esc(title)} being played, then try it yourself in your browser.</p>'
            f'<ul>{items}</ul><!--/ps:shots--></section>\n')


def render_game_page(f, catalog, guides=None, total_games=None):
    slug, title, category = f["slug"], f["title"], f["category"]
    genre_label = S.pretty_genre(category)
    genre_href = S.category_url(category)
    thumb = f.get("thumbnail") or ""
    similar = similar_games(slug, category, catalog, 18)
    rail_list, shelf_list = similar[:6], similar[6:18]
    if len(shelf_list) < 6:
        shelf_list = similar[:12]

    # Fan-made Scratch remakes get a one-line disclosure under the description.
    credit = f" by {FAN_MADE_CREDIT[slug]}" if slug in FAN_MADE_CREDIT else ""
    fan_made_note = f'<p class="how-to">This is a fan-made Scratch remake{credit}, not the original game.</p>\n' if slug in FAN_MADE_SLUGS else ""
    embed_url = EMBED_URL_OVERRIDE.get(slug, f["embed_url"])
    src_link = (f'<p class="how-to embed-source"><a href="{esc(EMBED_SOURCE_LINK[slug][1])}" rel="noopener" target="_blank">'
                f'{esc(EMBED_SOURCE_LINK[slug][0])}</a></p>\n') if slug in EMBED_SOURCE_LINK else ""
    embed_note = f'<p class="rd-note embed-note"><strong>{EMBED_NOTES[slug]}</strong></p>\n' if slug in EMBED_NOTES else ""
    if slug in GAME_NOTICES:
        embed_note = f'<p class="rd-note game-notice">{GAME_NOTICES[slug]}</p>\n' + embed_note
    extra_faq = ""
    if slug in GAME_FAQ_EXTRA:
        extra_faq = (f'<section class="game-faq" aria-labelledby="game-faq-title"><h2 id="game-faq-title">FAQ</h2>'
                     + "".join(f"<h3>{esc(q)}</h3><p>{esc(a)}</p>" for q, a in GAME_FAQ_EXTRA[slug])
                     + "</section>\n")
    af = ANSWER_FIRST.get(slug)
    last_updated_html = ""
    similar_html = ""
    h1_text = title
    if af:
        h1_text = af["h1"]
        f = dict(f)
        f["title_tag"] = esc(af["title_tag"])
        f["meta_desc"] = esc(af["meta_desc"])
        extra_faq = (f'<section class="game-faq" aria-labelledby="game-faq-title"><h2 id="game-faq-title">FAQ</h2>'
                     + "".join(f"<h3>{esc(q)}</h3><p>{esc(a)}</p>" for q, a in af["faq"])
                     + "</section>\n")
        _lu = {"2026-11-10": "10 Nov 2026", "2026-11-11": "11 Nov 2026"}.get(af["date_modified"], "11 Nov 2026")
        last_updated_html = f'<p class="last-updated">Last updated: {_lu}</p>\n'
        by_slug_af = {g["slug"]: g for g in catalog}
        sim_links = []
        for s2 in af.get("similar", [])[:3]:
            g2 = by_slug_af.get(s2)
            if g2:
                sim_links.append(f'<a href="/games/{esc(s2)}.html">{esc(g2.get("title") or s2)}</a>')
        if sim_links:
            similar_html = ('<p class="similar-inline"><strong>Similar games:</strong> '
                            + " · ".join(sim_links) + "</p>\n")
        new_blocks = []
        for block in f.get("jsonld") or []:
            try:
                data = json.loads(block)
            except (ValueError, TypeError):
                new_blocks.append(block)
                continue
            if data.get("@type") == "VideoGame":
                data["name"] = ("Brawl Stars (unofficial browser arena game)" if slug == "brawl-stars" else title)
                data["description"] = af["lead"]
                data["dateModified"] = af["date_modified"]
                data["url"] = f"{DOMAIN}/games/{slug}.html"
                new_blocks.append(json.dumps(data, ensure_ascii=False))
            elif data.get("@type") == "FAQPage":
                continue
            else:
                new_blocks.append(block)
        new_blocks.append(json.dumps({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
                for q, a in af["faq"]
            ],
        }, ensure_ascii=False))
        f["jsonld"] = new_blocks
    if not similar_html:
        sim_links = []
        for g2 in (rail_list or similar or [])[:3]:
            s2 = g2.get("slug")
            if s2 and s2 != slug:
                sim_links.append(f'<a href="/games/{esc(s2)}.html">{esc(g2.get("title") or s2)}</a>')
        if sim_links:
            similar_html = ('<p class="similar-inline"><strong>Similar games:</strong> '
                            + " · ".join(sim_links) + "</p>\n")
    landing = slug in LANDING_PAGES
    iframe_extra = ' id="game-frame" loading="eager" fetchpriority="high"' if landing else ""
    play_now = ('<a class="btn btn--primary play-now-btn" href="#game-frame" '
                'onclick="var f=document.getElementById(\'game-frame\');f.scrollIntoView({block:\'center\'});f.focus();return false;">'
                f'{S.icon("play", "i i--sm")}<span>Play now</span></a>\n') if landing else ""
    landing_related = _landing_related(slug, catalog) if landing else ""
    description = (f.get("description_html") or "").strip()
    if not text(description):
        description = esc(f"{title} is a free {genre_label.lower()} game you can play right here in your browser — no download, no sign-up.")
    if af:
        description = esc(af["lead"])

    og_title = af["title_tag"].split("|")[0].strip() if af else f"{title} — Pixelsprout"
    og = [
        '<meta property="og:type" content="website">',
        f'<meta property="og:title" content="{esc(og_title)}">',
        f'<meta property="og:description" content="{f["meta_desc"]}">',
        f'<meta property="og:url" content="{DOMAIN}/games/{esc(slug)}.html">',
    ]
    if thumb:
        og.append(f'<meta property="og:image" content="{esc(thumb)}">')
    og.append('<meta name="twitter:card" content="summary">')

    head = S.head(
        f["title_tag"], f["meta_desc"],
        canonical=f"{DOMAIN}/games/{slug}.html",
        meta_extra="\n".join(og),
        jsonld=f["jsonld"],
        head_extra=GAME_MOBILE_CSS + (LANDING_HEAD if landing else "")
        + '<link rel="preconnect" href="https://img.gamepix.com">\n<link rel="preconnect" href="https://img.gamemonetize.com">',
    )

    crumb_items = [("Home", "/")]
    if genre_href:
        crumb_items.append((esc(genre_label), genre_href))
    crumb_items.append((esc(title), None))

    chip = (
        f'<a class="chip" href="{genre_href}"><span class="dot" style="--dot:{S.genre_color(category)}"></span>{esc(genre_label)}</a>'
        if genre_href else
        f'<span class="chip"><span class="dot" style="--dot:{S.genre_color(category)}"></span>{esc(genre_label)}</span>'
    )

    mini = "".join(
        f'<li><a href="/games/{esc(g["slug"])}.html"><img src="{esc(S.thumb_of(g))}" alt="" width="48" height="48" loading="lazy" decoding="async">'
        f'<span><b>{esc(g["title"])}</b><small>{esc(S.pretty_genre(g.get("category", "")))}</small></span></a></li>'
        for g in rail_list
    )

    guides_html = ""
    if guides:
        rows = []
        for key, label, icon_name in GUIDE_LABELS:
            url = guides.get(key)
            if not url:
                continue
            shown = f"Games like {title}" if key == "similar" else label
            rows.append(
                f'<li><a href="{esc(url)}">{S.icon(icon_name)}<span class="link-list__label">{esc(shown)}</span>{S.icon("chevron-right", "i i--sm")}</a></li>'
            )
        hub_url = curated_hub_url(slug)
        if hub_url:
            rows.append(
                f'<li><a href="{esc(hub_url)}">{S.icon("layers")}<span class="link-list__label">{esc(f"Games like {title}: full list")}</span>{S.icon("chevron-right", "i i--sm")}</a></li>'
            )
        if rows:
            guides_html = (
                f'<section aria-labelledby="guides-title"><h2 class="block__title" id="guides-title">Guides for {esc(title)}</h2>'
                f'<ul class="link-list">{"".join(rows)}</ul></section>'
            )

    shelf_cards = "".join(S.card(g) for g in shelf_list)
    more_like = S.shelf("More like this", shelf_cards, more_href=genre_href,
                        more_label=f"All {esc(genre_label.lower())} games" if genre_href else "See all",
                        section_id="more-like-this")

    main = f"""<div class="game-layout">
<div class="game-main">
{S.crumbs(crumb_items)}
{embed_note}<div class="stage"><div class="embed-frame-wrap"><iframe src="{embed_url}" allow="fullscreen; autoplay; screen-orientation;" scrolling="no" allowfullscreen title="{esc(title)}"{iframe_extra}></iframe></div></div>
{landing_related}{src_link}<div class="game-bar">
<div class="game-bar__info"><h1 class="game-bar__title">{esc(h1_text)}</h1>{chip}</div>
<div class="game-bar__actions">
{play_now}<button class="btn btn--ghost fullscreen-btn" type="button" onclick="{f['onclick']}">{S.icon('maximize')}<span>Fullscreen</span></button>
<button class="icon-btn icon-btn--outline" type="button" data-fav aria-pressed="false" aria-label="Add to favorites">{S.icon('heart')}</button>
<button class="icon-btn icon-btn--outline" type="button" data-share aria-label="Share this game">{S.icon('share')}</button>
</div>
</div>
</div>
<aside class="game-rail" aria-label="Sponsored and related games">
{S.ad_rect()}
<div class="game-rail__more"><h2 class="eyebrow">More like this</h2><ul class="mini-list">{mini}</ul></div>
</aside>
<div class="game-body">
<section class="game-about" aria-labelledby="about-title">
<h2 id="about-title">About {esc(title)}</h2>
<p class="how-to"><!--ps:desc-->{description}<!--/ps:desc--></p>
{fan_made_note}{similar_html}{last_updated_html}<div class="facts"><span class="chip">Free to play</span><span class="chip">No download</span><span class="chip">Plays in your browser</span></div>
</section>
{extra_faq}{_social_section(slug, title)}{S.ad_native()}
{guides_html}
{S.ad_adsense(S.AD_SLOT_BANNER)}
{more_like}
{S.ad_leader(mobile="skip")}
{S.ad_adsense(S.AD_SLOT_RIBBON)}
</div>
</div>"""

    body_attrs = (
        f'class="game-page" data-page="game" data-slug="{esc(slug)}" data-title="{esc(title)}" '
        f'data-genre="{esc(category)}" data-thumbnail="{esc(thumb)}"'
    )
    if slug in FAQ_FREE_ANSWER:
        body_attrs += f' data-faq-free="{esc(FAQ_FREE_ANSWER[slug])}"'
    if slug in FAQ_MOBILE_ANSWER:
        body_attrs += f' data-faq-mobile="{esc(FAQ_MOBILE_ANSWER[slug])}"'
    out = S.page(head, main, body_attrs=body_attrs, active=genre_href, total_games=total_games)
    if slug in S.load_excluded_slugs() and S.NOINDEX_TAG not in out:  # excluded-games.json: keep noindex
        out = out.replace("<head>", "<head>\n" + S.NOINDEX_TAG, 1)
    return out


# ---------------------------------------------------------------------------
# Guide pages
# ---------------------------------------------------------------------------

def _parse_crumbs_legacy(fragment):
    items = [(label.strip(), href) for href, label in re.findall(r'<a href="([^"]*)"[^>]*>(.*?)</a>', fragment, re.DOTALL)]
    tail = re.sub(r"<a [^>]*>.*?</a>", "", fragment, flags=re.DOTALL)
    tail = tail.replace("/", " ").strip()
    if tail:
        items.append((tail, None))
    return items


def _parse_crumbs_new(fragment):
    items = [(label.strip(), href) for href, label in re.findall(r'<a href="([^"]*)">(.*?)</a>', fragment, re.DOTALL)]
    cur = _first(r'<span aria-current="page">(.*?)</span>', fragment)
    if cur:
        items.append((cur.strip(), None))
    return items


def _normalize_crumbs(items):
    out = []
    for label, href in items:
        if href in ("/index.html", "index.html", "../index.html"):
            href = "/"
        out.append((label, href))
    return out


def _parse_card_links(fragment):
    """Game links rendered as legacy similar-cards or current cards."""
    games = []
    for href, inner in re.findall(r'<a class="(?:similar-card|card)[^"]*" href="([^"]*)"[^>]*>(.*?)</a>', fragment, re.DOTALL):
        m = re.match(r"/games/([^/]+)\.html$", href)
        if not m:
            continue
        img = _first(r'<img src="([^"]*)"', inner)
        title = _first(r'<span class="card__title">(.*?)</span>', inner) or _first(r"<span>(.*?)</span>", inner)
        games.append({"slug": m.group(1), "title": text(title), "thumbnail": "" if img.endswith("logo-icon.png") else img})
    return games


def extract_guide_page(doc):
    f = {
        "title_tag": _first(r"<title>(.*?)</title>", doc).strip(),
        "meta_desc": _first(r'<meta name="description" content="([^"]*)"', doc),
        "canonical": _first(r'<link rel="canonical" href="([^"]*)"', doc),
        "og": _og_meta(doc),
        "jsonld": _jsonld_blocks(doc),
    }
    if _body_attr(doc, "data-page") == "guide":
        f["crumbs"] = _parse_crumbs_new(_first(r'<nav class="crumbs"[^>]*>(.*?)</nav>', doc))
        f["h1"] = _first(r'<h1 class="page-title">(.*?)</h1>', doc)
        f["eyebrow"] = _region("eyebrow", doc)
        f["game_slug"] = _body_attr(doc, "data-game-slug")
        f["game_title"] = text(_body_attr(doc, "data-game-title"))
        f["genre"] = text(_body_attr(doc, "data-genre"))
        f["thumbnail"] = text(_body_attr(doc, "data-thumbnail"))
        f["body"] = _region("body", doc)
        f["siblings"] = re.findall(r'<a href="([^"]*)">.*?<span class="link-list__label">(.*?)</span>', _region("siblings", doc), re.DOTALL)
        f["related"] = _parse_card_links(_region("related", doc))
        return f

    f["crumbs"] = _normalize_crumbs(_parse_crumbs_legacy(_first(r'<div class="breadcrumb"[^>]*>(.*?)</div>', doc)))
    f["h1"] = _first(r'<h1 class="game-title">(.*?)</h1>', doc)
    f["eyebrow"] = _first(r'<div class="game-meta">(.*?)</div>', doc)
    back = re.search(r'<a class="back-link" href="/games/([^"]+)\.html">&larr; back to (.*?)</a>', doc)
    f["game_slug"] = back.group(1) if back else ""
    f["game_title"] = text(back.group(2)) if back else ""
    genre = text(f["eyebrow"]).split("·")[-1].strip()
    f["genre"] = genre
    frame = doc.split('<div class="game-board-frame">', 1)[-1]
    img = _first(r'<img src="([^"]*)"', frame)
    f["thumbnail"] = "" if (not img or img.endswith("logo-icon.png")) else img
    body = _first(r'<article class="guide-body"[^>]*>(.*?)</article>', doc).strip()
    body = re.sub(r'\s+style="[^"]*"', "", body)
    body = re.sub(r'<p>\s*<a href="(/[^"]+/)">Browse more (.*?) &rarr;</a></p>', r'<p><a href="\1">Browse more \2 &rarr;</a></p>', body)
    f["body"] = body
    blocks = re.findall(r'<div class="similar-games">(.*?)</div>\s*</div>', doc, re.DOTALL)
    siblings, related = [], []
    for block in blocks:
        if "More about" in block:
            siblings = re.findall(r'<a class="similar-card" href="([^"]*)"><span>(.*?)</span></a>', block)
        else:
            related = _parse_card_links(block)
    f["siblings"] = siblings
    f["related"] = related
    return f


def render_guide_page(f, catalog_by_slug=None, total_games=None):
    catalog_by_slug = catalog_by_slug or {}
    game_slug = f.get("game_slug", "")
    game_title = f.get("game_title", "")
    genre = f.get("genre", "")
    thumb = f.get("thumbnail") or ""
    game_entry = catalog_by_slug.get(game_slug, {})
    if not thumb:
        thumb = game_entry.get("thumbnail", "")
    genre_label = S.pretty_genre(genre) if genre else ""

    head = S.head(f["title_tag"], f["meta_desc"], canonical=f.get("canonical") or None,
                  meta_extra=f.get("og", ""), jsonld=f.get("jsonld", []))

    game_card = ""
    if game_slug:
        game_card = (
            '<div class="game-card">'
            f'<a class="game-card__art" href="/games/{esc(game_slug)}.html" tabindex="-1" aria-hidden="true">'
            f'<img src="{esc(thumb or S.FALLBACK_THUMB)}" alt="" width="88" height="88" decoding="async"></a>'
            f'<div class="game-card__body"><span class="game-card__title">{esc(game_title)}</span>'
            f'<span class="game-card__meta">{esc(genre_label)}{" · " if genre_label else ""}Free to play</span></div>'
            f'<a class="btn btn--primary" href="/games/{esc(game_slug)}.html">{S.icon("play", "i i--sm")}Play now</a>'
            "</div>"
        )

    sibling_rows = []
    icons = {"Tips": "lightbulb", "Controls": "keyboard", "Beginner": "flag-start", "FAQ": "help", "Games Like": "layers"}
    for href, label in f.get("siblings", []):
        icon_name = next((v for k, v in icons.items() if text(label).startswith(k)), "book")
        sibling_rows.append(
            f'<li><a href="{esc(href)}">{S.icon(icon_name)}<span class="link-list__label">{label}</span>{S.icon("chevron-right", "i i--sm")}</a></li>'
        )
    siblings_html = ""
    if sibling_rows:
        siblings_html = (
            f'<section class="block" aria-labelledby="more-about"><h2 class="block__title" id="more-about">More about {esc(game_title)}</h2>'
            f'<ul class="link-list"><!--ps:siblings-->{"".join(sibling_rows)}<!--/ps:siblings--></ul></section>'
        )

    related_cards = []
    for g in f.get("related", []):
        entry = dict(catalog_by_slug.get(g["slug"], {}))
        entry.setdefault("slug", g["slug"])
        entry["title"] = g.get("title") or entry.get("title", g["slug"])
        entry["thumbnail"] = g.get("thumbnail") or entry.get("thumbnail", "")
        related_cards.append(S.card(entry))
    related_html = ""
    if related_cards:
        related_html = S.shelf("Play something similar", "<!--ps:related-->" + "".join(related_cards) + "<!--/ps:related-->",
                               more_href=S.category_url(genre) if genre else None, section_id="related",
                               extra_class="block")

    hub_html = ""
    hub_url = curated_hub_url(game_slug) if game_slug else None
    if hub_url and (f.get("canonical") or "").rstrip("/").endswith(f"/guides/games-like-{game_slug}"):
        hub_html = (f'<p class="block"><a class="btn btn--primary" href="{esc(hub_url)}">{S.icon("layers", "i i--sm")}'
                    f'See the full list: Games like {esc(game_title)}</a></p>\n')

    main = f"""<article class="reading">
{S.crumbs(f['crumbs'])}
<header class="page-head">
<p class="eyebrow"><!--ps:eyebrow-->{f.get('eyebrow', '')}<!--/ps:eyebrow--></p>
<h1 class="page-title">{f['h1']}</h1>
</header>
{game_card}
{S.ad_adsense(S.AD_SLOT_BANNER)}
<div class="prose"><!--ps:body-->{f['body']}<!--/ps:body--></div>
{hub_html}<div class="block">{S.ad_native()}</div>
{siblings_html}
{related_html}
<div class="block">{S.ad_rect()}</div>
</article>"""

    body_attrs = (
        f'class="guide-page" data-page="guide" data-game-slug="{esc(game_slug)}" data-game-title="{esc(game_title)}" '
        f'data-genre="{esc(genre)}" data-thumbnail="{esc(thumb)}"'
    )
    return S.page(head, main, body_attrs=body_attrs, active="/guides/", total_games=total_games)


# ---------------------------------------------------------------------------
# Collection pages (games-like/*)
# ---------------------------------------------------------------------------

def extract_collection_page(doc):
    f = {
        "title_tag": _first(r"<title>(.*?)</title>", doc).strip(),
        "meta_desc": _first(r'<meta name="description" content="([^"]*)"', doc),
        "canonical": _first(r'<link rel="canonical" href="([^"]*)"', doc),
        "og": _og_meta(doc),
        "jsonld": _jsonld_blocks(doc),
    }
    if _body_attr(doc, "data-page") == "collection":
        f["h1"] = _first(r'<h1 class="page-title">(.*?)</h1>', doc)
        f["intro"] = _region("intro", doc)
        f["games"] = _parse_card_links(_region("games", doc))
    else:
        f["h1"] = _first(r'<h1 class="game-title">(.*?)</h1>', doc)
        f["intro"] = _first(r'<div class="game-board-frame">\s*<p[^>]*>(.*?)</p>', doc).strip()
        f["games"] = _parse_card_links(_first(r'<div class="similar-games-grid">(.*?)</div>', doc))
    return f


def render_collection_page(f, catalog_by_slug=None, total_games=None):
    catalog_by_slug = catalog_by_slug or {}
    head = S.head(f["title_tag"], f["meta_desc"], canonical=f.get("canonical") or None,
                  meta_extra=f.get("og", ""), jsonld=f.get("jsonld", []))
    cards = []
    for g in f["games"]:
        entry = dict(catalog_by_slug.get(g["slug"], {}))
        entry.setdefault("slug", g["slug"])
        entry["title"] = g.get("title") or entry.get("title", g["slug"])
        entry["thumbnail"] = g.get("thumbnail") or entry.get("thumbnail", "")
        cards.append(S.card(entry))
    main = f"""<div class="reading reading--wide">
{S.crumbs([("Home", "/"), (f['h1'], None)])}
<header class="page-head">
<p class="eyebrow">Collection</p>
<h1 class="page-title">{f['h1']}</h1>
<p class="page-lede"><!--ps:intro-->{f['intro']}<!--/ps:intro--></p>
<p class="page-meta">{len(cards)} games</p>
</header>
{S.ad_adsense(S.AD_SLOT_BANNER)}
<div class="grid"><!--ps:games-->{''.join(cards)}<!--/ps:games--></div>
<div class="block">{S.ad_native()}</div>
<div class="block">{S.ad_rect()}</div>
</div>"""
    return S.page(head, main, body_attrs='class="collection-page" data-page="collection"', total_games=total_games)


# ---------------------------------------------------------------------------
# Hand-picked "games like" hubs (content-engine/collections_data.py CURATED_HUBS)
# ---------------------------------------------------------------------------

def _curated_hubs():
    """CURATED_HUBS from content-engine/collections_data.py, keyed by the main game's slug."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "content-engine", "collections_data.py")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_ps_collections_data", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return {h["game_slug"]: h for h in getattr(mod, "CURATED_HUBS", [])}
    except (OSError, ImportError, SyntaxError):
        return {}


def curated_hub_url(game_slug):
    hub = _curated_hubs().get(game_slug)
    return f"/games-like/{hub['slug']}/" if hub else None


def _game_card_row(entry, button_label="Play now"):
    slug = entry["slug"]
    genre_label = S.pretty_genre(entry.get("category", "")) if entry.get("category") else ""
    return (
        '<div class="game-card">'
        f'<a class="game-card__art" href="/games/{esc(slug)}.html" tabindex="-1" aria-hidden="true">'
        f'<img src="{esc(S.thumb_of(entry) or S.FALLBACK_THUMB)}" alt="" width="88" height="88" loading="lazy" decoding="async"></a>'
        f'<div class="game-card__body"><span class="game-card__title">{esc(entry["title"])}</span>'
        f'<span class="game-card__meta">{esc(genre_label)}{" · " if genre_label else ""}Free to play</span></div>'
        f'<a class="btn btn--primary" href="/games/{esc(slug)}.html">{S.icon("play", "i i--sm")}{esc(button_label)}</a>'
        "</div>"
    )


def render_curated_hub(hub, catalog_by_slug, total_games=None):
    """Full HTML for a hand-picked hub. Text is plain (escaped here), never generated."""
    path = f"/games-like/{hub['slug']}/"
    main_entry = dict(catalog_by_slug.get(hub["game_slug"], {}), slug=hub["game_slug"])
    main_entry.setdefault("title", hub["display_name"])
    listed = []
    for sec in hub["sections"]:
        for g in sec["games"]:
            e = dict(catalog_by_slug.get(g["slug"], {}), slug=g["slug"])
            e["title"] = g["title"]
            listed.append(e)
    schema = [
        json.dumps({"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": g["title"], "url": f"{DOMAIN}/games/{g['slug']}.html"}
            for i, g in enumerate(listed)]}),
        json.dumps({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{DOMAIN}/"},
            {"@type": "ListItem", "position": 2, "name": hub["h1"], "item": f"{DOMAIN}{path}"}]}),
    ]
    og_image = next((S.thumb_of(g) for g in [main_entry] + listed if S.thumb_of(g)), f"{DOMAIN}/assets/logo-icon.png")
    og = "\n".join([
        '<meta property="og:type" content="article">',
        f'<meta property="og:url" content="{DOMAIN}{path}">',
        f'<meta property="og:title" content="{esc(hub["title_tag"])}">',
        f'<meta property="og:description" content="{esc(hub["meta_desc"])}">',
        f'<meta property="og:image" content="{esc(og_image)}">',
    ])
    head = S.head(esc(hub["title_tag"]), esc(hub["meta_desc"]), canonical=f"{DOMAIN}{path}",
                  meta_extra=og, jsonld=schema)
    para = lambda t: f"<p>{esc(t)}</p>"
    parts = [para(t) for t in hub["intro"]]
    parts.append(f"<h2>{esc(hub['tips_title'])}</h2>")
    if hub.get("tips_intro"):
        parts.append(para(hub["tips_intro"]))
    parts.append("<ul>" + "".join(f"<li>{esc(t)}</li>" for t in hub["tips"]) + "</ul>")
    parts.append(f"<h2>{esc(hub['next_title'])}</h2>")
    parts += [para(t) for t in hub["next_intro"]]
    for sec in hub["sections"]:
        parts.append(f"<h2>{esc(sec['heading'])}</h2>")
        for g in sec["games"]:
            e = dict(catalog_by_slug.get(g["slug"], {}), slug=g["slug"])
            e["title"] = g["title"]
            parts.append(f"<h3>{esc(g['title'])}</h3>")
            parts.append(_game_card_row(e))
            parts.append(para(g["note"]))
    parts.append(f"<h2>{esc(hub['pick_title'])}</h2>")
    parts += [para(t) for t in hub["pick"]]
    if hub.get("footer_note"):
        parts.append(para(hub["footer_note"]))
    body = "\n".join(parts)
    more_rows = "".join(
        f'<li><a href="{esc(href)}">{S.icon("chevron-right")}<span class="link-list__label">{esc(label)}</span>{S.icon("chevron-right", "i i--sm")}</a></li>'
        for label, href in hub["more_links"])
    main = f"""<article class="reading">
{S.crumbs([("Home", "/"), (esc(hub['h1']), None)])}
<header class="page-head">
<p class="eyebrow">Collection</p>
<h1 class="page-title">{esc(hub['h1'])}</h1>
</header>
{_game_card_row(main_entry, "Play " + main_entry["title"])}
{S.ad_adsense(S.AD_SLOT_BANNER)}
<div class="prose"><!--ps:hub-->{body}<!--/ps:hub--></div>
<div class="block">{S.ad_native()}</div>
<section class="block" aria-labelledby="more-from"><h2 class="block__title" id="more-from">More from Pixelsprout</h2><ul class="link-list">{more_rows}</ul></section>
<div class="block">{S.ad_rect()}</div>
</article>"""
    return S.page(head, main, body_attrs=f'class="collection-page" data-page="curated-hub" data-curated="1" data-game-slug="{esc(hub["game_slug"])}"',
                  total_games=total_games)


# ---------------------------------------------------------------------------
# Legal / company pages
# ---------------------------------------------------------------------------

def extract_legal_page(doc):
    f = {
        "title_tag": _first(r"<title>(.*?)</title>", doc).strip(),
        "meta_desc": _first(r'<meta name="description" content="([^"]*)"', doc),
        "canonical": _first(r'<link rel="canonical" href="([^"]*)"', doc),
        "og": _og_meta(doc),
        "jsonld": _jsonld_blocks(doc),
    }
    if _body_attr(doc, "data-page") == "legal":
        f["h1"] = _first(r'<h1 class="page-title">(.*?)</h1>', doc)
        f["body"] = _region("body", doc)
        return f
    inner = _first(r'<div class="legal">(.*?)</div>\s*</body>', doc)
    inner = re.sub(r'<div class="legal-nav">.*?</div>', "", inner, flags=re.DOTALL)
    f["h1"] = _first(r"<h1>(.*?)</h1>", inner)
    inner = re.sub(r"<h1>.*?</h1>", "", inner, count=1, flags=re.DOTALL)
    inner = re.sub(r'<p><a href="/">← back to Pixelsprout</a></p>', "", inner)
    f["body"] = inner.strip()
    return f


def render_legal_page(f, total_games=None):
    head = S.head(f["title_tag"], f["meta_desc"], canonical=f.get("canonical") or None,
                  meta_extra=f.get("og", ""), jsonld=f.get("jsonld", []))
    main = f"""<article class="reading">
{S.crumbs([("Home", "/"), (f['h1'], None)])}
<header class="page-head"><h1 class="page-title">{f['h1']}</h1></header>
<div class="prose"><!--ps:body-->{f['body']}<!--/ps:body--></div>
</article>"""
    return S.page(head, main, body_attrs='class="legal-page" data-page="legal"', total_games=total_games)


# ---------------------------------------------------------------------------
# Legacy-body pages (tool pages, built-in games): new shell, original body
# ---------------------------------------------------------------------------

_OLD_FONT_LINK = re.compile(r'<link rel="stylesheet" href="https://fonts\.googleapis\.com/css2\?family=Press\+Start\+2P[^"]*">\n?')
_OLD_STYLES_LINK = re.compile(r'<link rel="stylesheet" href="(?:\.\./|/)?styles\.css(?:\?v=[^"]*)?">')


def wrap_legacy_page(doc, total_games=None, active=None):
    doc = re.sub(r"<!--ps:top-->.*?<!--/ps:top-->\n?", "", doc, flags=re.DOTALL)
    doc = re.sub(r"<!--ps:bottom-->.*?<!--/ps:bottom-->\n?", "", doc, flags=re.DOTALL)
    doc = re.sub(r'<link rel="stylesheet" href="' + re.escape(S.FONTS_HREF) + r'">\n?', "", doc)
    doc = re.sub(r'<link rel="stylesheet" href="' + re.escape(S.WORDMARK_FONT_HREF) + r'">\n?', "", doc)
    doc = _OLD_FONT_LINK.sub("", doc)
    doc = fix_jsonld_urls(doc)
    doc = re.sub(r'<link rel="preconnect" href="https://fonts\.(?:googleapis|gstatic)\.com"(?: crossorigin)?>\n?', "", doc)
    fonts = (
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        f'<link rel="stylesheet" href="{S.FONTS_HREF}">\n'
        f'<link rel="stylesheet" href="{S.WORDMARK_FONT_HREF}">\n'
        f'<link rel="stylesheet" href="/styles.css?v={S.ASSET_VERSION}">'
    )
    doc, n = _OLD_STYLES_LINK.subn(lambda m: fonts, doc, count=1)
    if not n:
        doc = doc.replace("</head>", fonts + "\n</head>", 1)
    if "/assets/ads.js" not in doc:
        doc = doc.replace("</head>", f'<script defer src="/assets/ads.js?v={S.ASSET_VERSION}"></script>\n</head>', 1)
    doc = re.sub(r'<script defer src="(?:\.\./|/)pixelsprout-features\.js(?:\?v=[^"]*)?"></script>',
                 f'<script defer src="/pixelsprout-features.js?v={S.ASSET_VERSION}"></script>', doc)

    top = (
        "<!--ps:top-->\n" + S.topbar(total_games) + "\n"
        f'<div class="shell">{S.rail(active)}\n<main id="main" class="main" tabindex="-1">\n'
        "<!--/ps:top-->\n"
    )
    bottom = "<!--ps:bottom-->\n</main></div>\n" + S.footer() + "\n<!--/ps:bottom-->\n"
    doc = re.sub(r"(<body[^>]*>\n?)", lambda m: m.group(1) + top, doc, count=1)
    idx = doc.rfind("</body>")
    doc = doc[:idx] + bottom + doc[idx:]
    return doc


# ---------------------------------------------------------------------------
# Category pages
# ---------------------------------------------------------------------------


# Topic guides (pages built from the 2026-11-10 pack). Linked from the guides hub, the homepage and category pages.
POPULAR_GUIDES = [
    ("Best free browser games with no download", "/guides/best-free-browser-games-no-download/"),
    ("Sites like Poki and CrazyGames", "/guides/sites-like-poki-crazygames/"),
    ("Games like Getting Over It (free online)", "/guides/games-like-getting-over-it-online-free/"),
    ("Free online puzzle games, no download", "/guides/free-online-puzzle-games-no-download/"),
    ("Free browser games for Chromebook", "/guides/browser-games-chromebook/"),
    ("Free horror games in your browser", "/guides/free-horror-games-browser-no-download/"),
    ("Games like Brawl Stars in your browser", "/guides/games-like-brawl-stars-browser/"),
    ("Browser games for school (no download)", "/guides/unblocked-games-to-play-at-school/"),
    ("Games like Among Us (free online)", "/guides/games-like-among-us-online-free/"),
    ("Two-player games like Fireboy and Watergirl", "/guides/two-player-games-like-fireboy-and-watergirl/"),
    ("Free games to play with friends online (no download)", "/guides/free-games-to-play-with-friends-online-no-download/"),
    ("Games to play when bored, online free", "/guides/games-to-play-when-bored-online-free/"),
]
HOME_GUIDES = [POPULAR_GUIDES[i] for i in (1, 3, 5, 4)]
CATEGORY_GUIDE_LINK = {
    "Puzzle": ("Read our guide to free online puzzle games with no download", "/guides/free-online-puzzle-games-no-download/"),
    "Multiplayer": ("Read our guide to games like Brawl Stars you can play in your browser", "/guides/games-like-brawl-stars-browser/"),
    "Horror": ("Read our guide to free browser horror games", "/guides/free-horror-games-browser-no-download/"),
}
HOME_FAQ = [
    ("Is Pixelsprout free?", "Yes. Every game on Pixelsprout is free to play. The site is supported by ads."),
    ("Do I need to download anything?", "No. Games are HTML5 and play directly in your browser tab. There is nothing to install."),
    ("Do I need an account?", "No. You don't need a Pixelsprout account to play."),
    ("Does Pixelsprout work on a Chromebook?",
     "Games are HTML5 and run in the Chrome browser, so they usually work on Chromebooks if your network allows the site. "
     "We have not tested every game on every model, and we do not help bypass school or work filters."),
    ("Does it work on phones?", "Games are built for mouse and keyboard on desktop and have not been tested on touch screens."),
    ("How many games are on Pixelsprout?", "More than 5,000 free browser games across puzzle, arcade, action, racing, sports, horror, multiplayer and many other genres."),
]
HOME_GENRE_LIST = [("Puzzle games", "/puzzle/"), ("Arcade games", "/arcade/"), ("Action games", "/action/"), ("Racing games", "/racing/"),
                   ("Sports games", "/sports/"), ("Horror games", "/horror/"), ("Multiplayer games", "/multiplayer/"),
                   ("Two Player games", "/two-player/"), ("Clicker games", "/clicker/"), ("Strategy games", "/strategy/"),
                   ("Kids games", "/kids/"), ("Casual games", "/casual/")]
# Official profiles only (same URLs the game pages already link to).
ORG_SAME_AS = ["https://x.com/Playpixelsprout", "https://www.instagram.com/playpixelsprout/"]


def _popular_guides_html():
    items = "".join(f'<li><a href="{h}">{esc(t)}</a></li>' for t, h in POPULAR_GUIDES)
    return (f'<section class="block featured-guides" aria-labelledby="popular-guides-title"><h2 class="block__title" id="popular-guides-title">'
            f'Popular guides</h2><ul>{items}</ul></section>')


def _popular_guides_jsonld():
    return json.dumps({
        "@context": "https://schema.org", "@type": "CollectionPage", "name": "Game guides", "url": f"{DOMAIN}/guides/",
        "mainEntity": {"@type": "ItemList", "name": "Featured guides", "itemListElement": [
            {"@type": "ListItem", "position": i, "name": t, "url": f"{DOMAIN}{h}"} for i, (t, h) in enumerate(POPULAR_GUIDES, 1)]},
    })


def _home_guides_html():
    links = " &middot; ".join(f'<a href="{h}">{esc(t)}</a>' for t, h in HOME_GUIDES)
    return (f'<section class="block home-guides" aria-labelledby="home-guides-title"><h2 class="block__title" id="home-guides-title">Guides</h2>'
            f'<p>{links}</p></section>')


# Hand-written intro shown above the game grid on selected category pages (links must be live game pages).
CATEGORY_INTRO_HTML = {
    "Horror": (
        '<section class="genre-intro block"><h2 class="block__title">Free horror games in your browser</h2>'
        "<p>Pixelsprout's horror games are short, free and open in a browser tab with no download. You will find "
        '<strong>escape-the-house stealth games</strong> (<a href="/games/granny-house-escape.html">Granny House Escape</a>, '
        '<a href="/games/scary-doll.html">Scary Doll</a>, <a href="/games/dr-psycho-hospital-escape.html">Dr. Psycho - Hospital Escape</a>), '
        '<strong>creepy point-and-click mysteries</strong> (<a href="/games/the-house-2.html">The House 2</a>, '
        '<a href="/games/exhibit-of-sorrows.html">Exhibit of Sorrows</a>) and <strong>horror shooters</strong> '
        '(<a href="/games/horror-sniper-clown-ghost.html">Horror Sniper Clown Ghost</a>). Some have jump scares, so they suit '
        "teens and adults. Tip: use headphones and full screen.</p></section>"
    ),
}

# Per-category FAQ replacing the default 4 questions ({count} is filled in). Visible FAQ and JSON-LD share this copy.
CATEGORY_FAQ = {
    "Horror": [
        ("Are these horror games free?",
         "Yes. Every horror game on this page is free to play in your browser on Pixelsprout, with no download or account."),
        ("Which horror game should I play first?",
         "For stealth and escape, start with Granny House Escape or Scary Doll. For something less intense, try The House 2 (point-and-click) or Escape From Work."),
        ("Do these games have jump scares?",
         "Some do, especially the chase-style escape games and Floppa Scary Horror. Point-and-click mysteries like The House 2 rely more on atmosphere."),
        ("Are these horror games OK for kids?",
         "Many have jump scares or creepy themes and are aimed at teens and adults. Younger players should try the Kids or Puzzle sections instead."),
        ("Do I need to download anything?",
         "No. These games run in the browser, so there is nothing to install."),
        ("Do they work on mobile?",
         "They are built for mouse and keyboard on desktop and have not been tested on touch screens, so some may not work on phones."),
        ("How many horror games are listed here?",
         "This page lists {count} free horror games you can open in your browser."),
    ],
}


def _category_guide_link(category):
    if category not in CATEGORY_GUIDE_LINK:
        return ""
    t, h = CATEGORY_GUIDE_LINK[category]
    return f'<p class="page-lede"><a href="{h}">{esc(t)}</a></p>\n'


def _category_faq_pairs(category, count):
    """Visible FAQ copy for category / A–Z pages (must match FAQPage JSON-LD exactly)."""
    if category in CATEGORY_FAQ:
        return [(q, a.replace("{count}", str(count))) for q, a in CATEGORY_FAQ[category]]
    label = S.pretty_genre(category) if category else "browser"
    label_l = label.lower()
    pairs = [
        ("Are these games free?",
         "Yes. Every game on this page is free to play in your browser on Pixelsprout."),
        ("Do I need to download anything?",
         "No. These games run in the browser — nothing to install."),
        ("Do they work on mobile?",
         "They are built for mouse and keyboard on desktop and have not been tested on touch screens, so some may not work on phones."),
        (f"How many {label_l} games are listed here?",
         f"This page lists {count} free {label_l} games you can open in your browser."),
    ]
    return pairs


def _faq_section_html(pairs, heading_id="category-faq-title"):
    items = "".join(
        f"<details><summary>{esc(q)}</summary><p>{esc(a)}</p></details>"
        for q, a in pairs
    )
    return (
        f'<section class="block category-faq" aria-labelledby="{heading_id}">'
        f'<h2 class="block__title" id="{heading_id}">FAQ</h2>{items}</section>'
    )


def _faq_jsonld(pairs):
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": q,
                "acceptedAnswer": {"@type": "Answer", "text": a},
            }
            for q, a in pairs
        ],
    })


def _itemlist_jsonld(name, games):
    elements = []
    for i, g in enumerate(games, 1):
        elements.append({
            "@type": "ListItem",
            "position": i,
            "name": g["title"],
            "url": f"{DOMAIN}/games/{g['slug']}.html",
        })
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": name,
        "numberOfItems": len(games),
        "itemListElement": elements,
    })


def category_intro(category, count):
    name = S.pretty_genre(category).lower()
    return (
        f"Browse {count} free {name} games, playable instantly in your browser. "
        f"No downloads, no accounts, no waiting — just click and play. New {name} "
        f"games are added regularly, so check back for more."
    )


def render_category_page(category, games, all_categories, total_games=None, newness=None):
    slug = S.slugify(category)
    canonical_url = f"{DOMAIN}/{slug}/"
    count = len(games)
    label = S.pretty_genre(category)
    if category == "Horror":
        title = "Play Horror Games Free Online, No Download | Pixelsprout"
        description = ("Play free horror games online in your browser on Pixelsprout — no download, no account. "
                       f"{count} short stealth, mystery and horror titles ready in the tab.")
    else:
        title = f"{category} Games - Free Online, No Download | Pixelsprout"
        description = f"Play {count} free {category.lower()} games online at Pixelsprout. No downloads, no accounts - instant browser play."
    breadcrumb_schema = json.dumps({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{DOMAIN}/"},
            {"@type": "ListItem", "position": 2, "name": category, "item": canonical_url},
        ],
    })
    faq_pairs = _category_faq_pairs(category, count)
    itemlist_schema = _itemlist_jsonld(f"{label} games on Pixelsprout", games)
    faq_schema = _faq_jsonld(faq_pairs)
    og = "\n".join([
        '<meta property="og:type" content="website">',
        f'<meta property="og:url" content="{canonical_url}">',
        f'<meta property="og:title" content="{esc(title)}">',
        f'<meta property="og:description" content="{esc(description)}">',
        f'<meta property="og:image" content="{DOMAIN}/assets/og-banner.jpg">',
    ])
    jsonld_list = [breadcrumb_schema, itemlist_schema, faq_schema]
    if category == "Horror":
        jsonld_list.insert(0, json.dumps({
            "@context": "https://schema.org", "@type": "CollectionPage",
            "name": "Horror games on Pixelsprout", "url": canonical_url,
            "dateModified": "2026-11-10", "description": description,
        }))
    head = S.head(esc(title), esc(description), canonical=canonical_url, meta_extra=og,
                  jsonld=jsonld_list,
                  head_extra='<link rel="preconnect" href="https://img.gamepix.com">\n<link rel="preconnect" href="https://img.gamemonetize.com">')

    page_size = 60
    cards = "".join(
        S.card(g, show_meta=False, index=i, extra_class="is-more" if i >= page_size else "",
               newness=(newness or {}).get(g["slug"], i))
        for i, g in enumerate(games)
    )
    related = []
    for other, other_count in all_categories:
        if S.slugify(other) == slug:
            continue
        url = S.category_url(other)
        if url and url != f"/{slug}/":
            related.append(f'<a class="chip" href="{url}"><span class="dot" style="--dot:{S.genre_color(other)}"></span>{esc(S.pretty_genre(other))}</a>')
    related_html = "".join(related[:24])

    main = f"""{S.crumbs([("Home", "/"), (esc(label), None)])}
<header class="page-head">
<p class="eyebrow"><span class="dot" style="--dot:{S.genre_color(category)}"></span> Genre</p>
<h1 class="page-title">{esc("Play Horror Games Free Online — No Download" if category == "Horror" else f"{label} games")}</h1>
<p class="page-lede">{esc("Play free horror games online in your browser on Pixelsprout — no download and no account. Short stealth escapes, creepy point-and-click mysteries and horror shooters load in the tab. Built for mouse and keyboard; touch play has not been tested for every title." if category == "Horror" else category_intro(category, count))}</p>
{('<p class="last-updated">Last updated: 10 Nov 2026</p>' if category == "Horror" else '')}
{_category_guide_link(category)}</header>
{CATEGORY_INTRO_HTML.get(category, "")}<div class="catalog-bar" data-catalog-bar>
<label class="field"><span class="visually-hidden">Filter {esc(label)} games</span>{S.icon('search')}<input type="search" data-catalog-filter placeholder="Filter {count} {esc(label.lower())} games" autocomplete="off"></label>
<div class="segmented" role="group" aria-label="Sort games">
<button type="button" data-sort="popular" aria-pressed="true">Popular</button>
<button type="button" data-sort="new" aria-pressed="false">Newest</button>
<button type="button" data-sort="az" aria-pressed="false">A–Z</button>
</div>
<span class="catalog-bar__count" data-catalog-count>{count} games</span>
</div>
<div class="grid grid--genre" id="catalog" data-catalog data-page-size="{page_size}">{cards}</div>
<p class="empty-state" data-catalog-empty hidden>No {esc(label.lower())} games match that. Try a shorter word, or <button class="btn btn--quiet" type="button" data-search-open>search every game</button>.</p>
<div class="load-more"{" hidden" if count <= page_size else ""}><button class="btn btn--ghost" type="button" data-load-more>Show more games</button></div>
<div class="block">{S.ad_native()}</div>
{_faq_section_html(faq_pairs)}
<section class="related-genres" aria-labelledby="more-genres"><h2 class="section-title" id="more-genres">More genres</h2><div class="chips">{related_html}</div></section>
<div class="block">{S.ad_leader(mobile="rect")}</div>"""
    return S.page(head, main, body_attrs='class="category-page" data-page="category"', active=f"/{slug}/", total_games=total_games)


# ---------------------------------------------------------------------------
# Homepage and A–Z
# ---------------------------------------------------------------------------

HOME_SHELVES = ["Puzzle", "Arcade", "Action", "Adventure", "Sports", "Racing", "Kids", "Casual",
                "Clicker", "Shooting", "Two-Player", "Multiplayer"]

POPULAR_FALLBACK = ["subway-princess-run", "worms-zone", "uno-online", "baba-is-you",
                    "getting-over-it", "suika-world", "tank-1990", "ultimate-solitaire-classic"]


def _group_by_primary(catalog):
    groups = {}
    for g in catalog:
        groups.setdefault(S.primary_category(g.get("category", "")), []).append(g)
    return groups


def _featured_pick(featured, by_slug, today=None):
    today = today or datetime.date.today()
    usable = [f for f in featured if f.get("slug") in by_slug]
    if not usable:
        return None, None
    pick = usable[today.toordinal() % len(usable)]
    return pick, by_slug[pick["slug"]]


def render_home(catalog, featured, popular_ranked, today=None):
    by_slug = {g["slug"]: g for g in catalog}
    groups = _group_by_primary(catalog)
    total = len(catalog)
    genre_pages = []
    for cat, games in groups.items():
        url = S.category_url(cat)
        if url and len(games) >= 3:
            genre_pages.append((cat, len(games), url))
    genre_pages.sort(key=lambda x: -x[1])

    pick, hero_game = _featured_pick(featured, by_slug, today)
    used = set()
    hero_html = ""
    # LCP with redesign is the GTA collage's first tile; preload + SSR it (don't wait on JS).
    preload = None
    ssr_collage_html = ""
    try:
        with open(os.path.join(S.ROOT, "assets/redesign/home.json"), encoding="utf-8") as _hf:
            _col = (json.load(_hf).get("collage") or [])
        if _col and _col[0].get("i"):
            preload = _col[0]["i"]
            if isinstance(preload, str) and preload.startswith("/") and not preload.startswith("//"):
                preload = f"{DOMAIN}{preload}"
        if len(_col) >= 3:
            tiles = []
            for i, g in enumerate(_col[:5]):
                src = g.get("i") or "/assets/logo-icon.webp"
                attrs = ' fetchpriority="high"' if i == 0 else ' loading="lazy"'
                tiles.append(
                    f'<a class="rd-collage__c rd-collage__c{i}" href="/games/{esc(g.get("s",""))}.html">'
                    f'<img src="{esc(src)}" alt="{esc(g.get("t") or "")}" width="400" height="250" '
                    f'decoding="async"{attrs}></a>'
                )
            ssr_collage_html = (
                '<div class="rd-collage rd-collage--ssr" aria-label="City action games">'
                + "".join(tiles) + "</div>"
            )
    except (OSError, ValueError, TypeError, AttributeError, IndexError, NameError):
        preload = None
        ssr_collage_html = ""
    if hero_game:
        used.add(hero_game["slug"])
        art = S.thumb_of(hero_game, size=480)
        genre_url = S.category_url(hero_game.get("category", ""))
        genre_label = S.pretty_genre(hero_game.get("category", ""))
        chip = (f'<a class="chip" href="{genre_url}"><span class="dot" style="--dot:{S.genre_color(hero_game.get("category", ""))}"></span>{esc(genre_label)}</a>'
                if genre_url else f'<span class="chip">{esc(genre_label)}</span>')
        hero_html = f"""<section class="hero" aria-labelledby="hero-title">
<div class="hero__bg" style="background-image:url('{esc(art)}')" aria-hidden="true"></div>
<div class="hero__body">
<p class="eyebrow">Featured today</p>
<h2 class="hero__title" id="hero-title"><a href="/games/{esc(hero_game['slug'])}.html">{esc(hero_game['title'])}</a></h2>
<p class="hero__pitch">{esc(pick.get('pitch', ''))}</p>
<div class="hero__meta">{chip}<span class="chip">Free · No download</span></div>
<div class="hero__cta"><a class="btn btn--primary" href="/games/{esc(hero_game['slug'])}.html">{S.icon('play', 'i i--sm')}Play now</a>{f'<a class="btn btn--ghost" href="{genre_url}">More {esc(genre_label.lower())} games</a>' if genre_url else ''}</div>
</div>
<a class="hero__art" href="/games/{esc(hero_game['slug'])}.html" tabindex="-1" aria-hidden="true"><img src="{esc(art)}" alt="" width="640" height="400" loading="lazy" decoding="async"></a>
</section>"""

    ranked = [by_slug[r["slug"]] for r in (popular_ranked or []) if r.get("slug") in by_slug]
    popular = ranked[:10] if len(ranked) >= 4 else [by_slug[s] for s in POPULAR_FALLBACK if s in by_slug]
    popular = [g for g in popular if g["slug"] not in used]
    popular_cards = "".join(S.tool_card(*t) for t in S.TOOL_CARDS) + "".join(S.card(g) for g in popular)
    used.update(g["slug"] for g in popular)

    shelves = []
    for cat in HOME_SHELVES:
        games = groups.get(cat, [])
        url = S.category_url(cat)
        if not games or not url:
            continue
        ordered = sorted((g for g in games if g.get("thumbnail") and g["slug"] not in used),
                         key=lambda g: (S.source_rank(g), games.index(g)))
        picks = ordered[:6]  # mobile weight: 6 cards/shelf (was 18)
        used.update(g["slug"] for g in picks)
        shelves.append(S.shelf(esc(S.pretty_genre(cat)), "".join(S.card(g, show_meta=False) for g in picks),
                               more_href=url, count=f"{len(games)} games", dot=S.genre_color(cat)))

    for pos, ad in ((3, f'<div class="shelf--ad">{S.ad_native("ad--panel")}</div>'),
                    (8, f'<div class="shelf--ad">{S.ad_leader(mobile="rect")}</div>')):
        if len(shelves) > pos:
            shelves.insert(pos, ad)

    genre_tiles = "".join(
        f'<a class="genre-tile" href="{url}">{S.icon(S.genre_icon(cat))}<span class="genre-tile__name">{esc(S.pretty_genre(cat))}</span><span class="genre-tile__count">{n}</span></a>'
        for cat, n, url in sorted(genre_pages, key=lambda x: S.pretty_genre(x[0]).lower())
    )

    title = "Play Free Browser Games Online, No Download | Pixelsprout"
    description = "Pixelsprout is a free browser-game portal with 5,000+ HTML5 games — no download and no account needed."
    meta_extra = "\n".join([
        '<meta name="p:domain_verify" content="119e6733e3c5887bb3f24d440ab498fa"/>',
        '<meta property="og:type" content="website">',
        '<meta property="og:url" content="https://www.playpixelsprout.com/">',
        '<meta property="og:title" content="Pixelsprout - Play. Grow. Explore.">',
        '<meta property="og:description" content="Pixelsprout is a free browser gaming library with thousands of games across every genre. No installs, no accounts -- just click and play.">',
        '<meta property="og:image" content="https://www.playpixelsprout.com/assets/og-banner.jpg">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="971">',
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta name="twitter:title" content="Pixelsprout - Play. Grow. Explore.">',
        '<meta name="twitter:description" content="Free browser gaming library with thousands of games. No installs, no accounts.">',
        '<meta name="twitter:image" content="https://www.playpixelsprout.com/assets/og-banner.jpg">',
    ])
    website_ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "Pixelsprout",
        "url": f"{DOMAIN}/",
    })
    organization_ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Pixelsprout",
        "url": f"{DOMAIN}/",
        "description": "Free browser-game portal with thousands of HTML5 games. No download, no account required.",
    })
    website_ld = json.dumps({"@context": "https://schema.org", "@type": "WebSite", "@id": f"{DOMAIN}/#website", "name": "Pixelsprout",
                             "alternateName": "Play Pixelsprout", "url": f"{DOMAIN}/", "description": "Pixelsprout is a free browser-game portal with 5,000+ HTML5 games — no download and no account needed.", "publisher": {"@id": f"{DOMAIN}/#org"}, "inLanguage": "en",
                             "dateModified": "2026-11-10"})
    organization_ld = json.dumps({"@context": "https://schema.org", "@type": "Organization", "@id": f"{DOMAIN}/#org", "name": "Pixelsprout",
                                  "url": f"{DOMAIN}/", "logo": f"{DOMAIN}/apple-touch-icon.png",
                                  "description": "Pixelsprout is a free browser-game portal with 5,000+ HTML5 games — no download and no account needed.",
                                  "sameAs": ORG_SAME_AS})
    genres_ld = json.dumps({"@context": "https://schema.org", "@type": "ItemList", "name": "Game genres on Pixelsprout", "itemListElement": [
        {"@type": "ListItem", "position": i, "name": t, "url": f"{DOMAIN}{h}"} for i, (t, h) in enumerate(HOME_GENRE_LIST, 1)]})
    home_faq_ld = _faq_jsonld(HOME_FAQ)
    # SearchAction omitted: site search is an in-page overlay (data-search-open), not a query-param URL.
    head = S.head(esc(title), esc(description), canonical=f"{DOMAIN}/", meta_extra=meta_extra,
                  jsonld=[organization_ld, website_ld, genres_ld, home_faq_ld], preload_image=preload,
                  head_extra='<link rel="preconnect" href="https://img.gamepix.com">\n<link rel="preconnect" href="https://img.gamemonetize.com">')

    continue_shelf = S.shelf("Continue playing", "", section_id="continue-playing", hidden=True,
                             track_id="continue-playing-track")
    popular_shelf = S.shelf("Popular now", popular_cards, section_id="popular-now")

    main = f"""{ssr_collage_html}<div class="home-intro">
<h1>Play Free Browser Games Online — No Download</h1>
<p>Play free browser games online on Pixelsprout with no download and no account. {total:,} HTML5 games across {len(genre_pages)} genres load in your tab — puzzle, arcade, action, horror, multiplayer and more. Built for mouse and keyboard; touch screens have not been tested for every title.</p>
<p class="last-updated">Last updated: 10 Nov 2026</p>
</div>
{continue_shelf}
{popular_shelf}
{''.join(shelves)}
<section class="shelf" id="all-genres" aria-labelledby="all-genres-title">
<div class="shelf__head"><h2 class="shelf__title" id="all-genres-title">Browse all genres</h2><span class="shelf__count">{len(genre_pages)} genres</span></div>
<div class="genre-grid">{genre_tiles}</div>
</section>
{_home_guides_html()}
{_faq_section_html(HOME_FAQ, heading_id="home-faq-title")}
<div class="home-foot"><a class="btn btn--ghost" href="/all-games/">{S.icon('list', 'i i--sm')}All games A–Z</a><a class="btn btn--ghost" href="/guides/">{S.icon('book', 'i i--sm')}Game guides</a></div>"""
    return S.page(head, main, body_attrs='class="home-page" data-page="home"', active="/", total_games=total)


def render_all_games(catalog):
    games = sorted(catalog, key=lambda g: g["title"].strip().lower())
    groups = {}
    for g in games:
        first = g["title"].strip()[:1].upper()
        key = first if first.isalpha() else "#"
        groups.setdefault(key, []).append(g)
    order = (["#"] if "#" in groups else []) + sorted(k for k in groups if k != "#")
    nav = "".join(f'<a href="#letter-{"num" if k == "#" else k}">{k}</a>' for k in order)
    sections = []
    for k in order:
        anchor = "num" if k == "#" else k
        items = "".join(
            f'<li><a href="/games/{esc(g["slug"])}.html">{esc(g["title"])}</a><span>{esc(S.pretty_genre(g.get("category", "")))}</span></li>'
            for g in groups[k]
        )
        sections.append(f'<section class="az-group" id="letter-{anchor}"><h2>{k}</h2><ul class="az-list">{items}</ul></section>')
    title = "All Games A–Z - Every Free Game on Pixelsprout"
    description = f"An alphabetical list of all {len(games):,} free browser games on Pixelsprout. No downloads, no accounts."
    faq_pairs = _category_faq_pairs("browser", len(games))
    # Genre-specific line for A–Z: rephrase the 4th question for this page
    faq_pairs[3] = (
        "How many games are listed here?",
        f"This page lists {len(games):,} free browser games in alphabetical order.",
    )
    breadcrumb_schema = json.dumps({
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{DOMAIN}/"},
            {"@type": "ListItem", "position": 2, "name": "All games A–Z", "item": f"{DOMAIN}/all-games/"},
        ]})
    head = S.head(esc(title), esc(description), canonical=f"{DOMAIN}/all-games/",
                  jsonld=[
                      breadcrumb_schema,
                      _itemlist_jsonld("All games on Pixelsprout", games),
                      _faq_jsonld(faq_pairs),
                  ])
    main = f"""{S.crumbs([("Home", "/"), ("All games A–Z", None)])}
<header class="page-head">
<h1 class="page-title">All games, A–Z</h1>
<p class="page-lede">Every game on Pixelsprout in one list — {len(games):,} of them. Looking for something specific? <button class="btn btn--quiet" type="button" data-search-open>Search instead</button></p>
</header>
<nav class="az-nav" aria-label="Jump to letter">{nav}</nav>
{''.join(sections)}
{_faq_section_html(faq_pairs)}"""
    return S.page(head, main, body_attrs='class="az-page" data-page="all-games"', active="/all-games/", total_games=len(games))


FEATURED_GUIDES = [
    ("Getting Over It", "getting-over-it", "A hammer-climbing challenge played with the mouse. This is a fan-made browser remake.",
     [("Short video on YouTube", "https://www.youtube.com/shorts/glZERu6lgBE"), ("Reel on Facebook", "https://www.facebook.com/reel/956487844203977"), ("Carousel on Instagram", "https://www.instagram.com/p/Dd8M05Tm-lc/"), ("Post on X", "https://x.com/Playpixelsprout/status/2105534668061958589")]),
    ("Bubble Up Endless", "bubble-up-endless", "A bubble shooter where you press and hold to aim, release to shoot, and chain combos before the red line.",
     [("Short video on YouTube", "https://youtube.com/shorts/zeVo-cKj_qE"), ("Reel on Facebook", "https://www.facebook.com/reel/1127971539689130"), ("Carousel on Instagram", "https://www.instagram.com/playpixelsprout/p/Dd-Bee5m7Fe/"), ("Post on X", "https://x.com/Playpixelsprout/status/2105787341965332762")]),
    ("Dot Link Puzzle", "dot-link-puzzle-connect-the-dots", "A connect-the-dots puzzle with boards from 5x5 up to 9x9, plus undo, reset and hint buttons.",
     [("Short video on YouTube", "https://youtube.com/shorts/OR3MsgtAMQU"), ("Reel on Facebook", "https://www.facebook.com/reel/2109658733245485"), ("Carousel on Instagram", "https://www.instagram.com/playpixelsprout/p/DeAmPNtmz50/"), ("Post on X", "https://x.com/Playpixelsprout/status/2106149730463957308")]),
    ("Baba Is You", "baba-is-you", "A turn-based puzzle where you push word blocks to change the rules. This is a fan-made browser remake.", []),
    ("2048 Cube Run", "2048-cube-run", "A three-lane runner where numbered cubes merge as you run toward the finish gate.", []),
    ("2048", "2048", "A 4x4 sliding-number puzzle played with the arrow keys: merge matching tiles to build higher numbers.", []),
    ("Toilet Rush: Draw to Pee", "toilet-rush-draw-to-pee", "A draw-a-path puzzle played with the mouse: drag a route from the character to the toilet.", []),
    ("Moonbloom", "moonbloom", "A Pixelsprout original puzzle: drop seeds into a 6x7 night planter and match three or more plants to grow them.", []),
    ("Skateboard Challenge", "skateboard-challenge", "A 2D street skating game: jump cones, collect coins and the letters of SKATE, and pull tricks for points.", []),
    ("Ninja Adventure", "ninja-adventure", "A side-scrolling ninja platformer: pick Ninja Boy or Ninja Girl on the player-select screen, then run Level 1 collecting coins.", []),
    ("Lumenstack", "lumenstack", "Time each drop so the sliding block overlaps the stack; only the overlap stays and a miss ends the run.", []),
]


def _featured_guides_html():
    out = ['<section aria-labelledby="featured-guides-title"><h2 class="block__title" id="featured-guides-title">Featured guides</h2>',
           '<p class="page-lede">Written after playing each game. Every game is free in your browser, with no download.</p>']
    for name, slug, blurb, social in FEATURED_GUIDES:
        links = [("Play", f"/games/{slug}.html"), ("Tips", f"/guides/{slug}-tips/"), ("Controls", f"/guides/{slug}-controls/"),
                 ("Beginner guide", f"/guides/{slug}-beginner-guide/"), ("FAQ", f"/guides/{slug}-faq/"), ("Similar games", f"/guides/games-like-{slug}/")]
        li = ' &middot; '.join(f'<a href="{h}">{esc(t)}</a>' for t, h in links)
        so = ''
        if social:
            so = '<p>' + ' &middot; '.join(f'<a href="{h}" rel="noopener" target="_blank">{esc(t)}</a>' for t, h in social) + '</p>'
        out.append(f'<h3>{esc(name)}</h3><p>{esc(blurb)}</p><p>{li}</p>{so}')
    out.append('</section>')
    return ''.join(out)


def _hub_hidden_slugs():
    """Games whose guides are all on content-engine/noindex-guides.json stay off the hub list."""
    try:
        sys.path.insert(0, os.path.join(S.ROOT, "content-engine"))
        import noindex_guides
        with open(os.path.join(S.ROOT, "guides-index.json"), encoding="utf-8") as f:
            return noindex_guides.hidden_hub_slugs(json.load(f))
    except (ImportError, OSError, ValueError):
        return []


def render_guides_hub(total_games=None):
    title = "Game Guides - Tips, Controls & FAQs | Pixelsprout"
    description = "Tips, controls, beginner guides, and FAQs for every game on Pixelsprout."
    head = S.head(esc(title), esc(description), canonical=f"{DOMAIN}/guides/", jsonld=[_popular_guides_jsonld()])
    main = f"""{S.crumbs([("Home", "/"), ("Guides", None)])}
<header class="page-head">
<p class="eyebrow">Read</p>
<h1 class="page-title">Game guides</h1>
<p class="page-lede">Tips, controls, beginner guides and FAQs for the games on Pixelsprout.</p>
</header>
{_popular_guides_html()}
{_featured_guides_html()}
<h2 class="block__title">All game guides</h2>
<div class="catalog-bar"><label class="field"><span class="visually-hidden">Filter guides</span>{S.icon('search')}<input id="guides-search" type="search" placeholder="Find a game's guides" autocomplete="off"></label><span class="catalog-bar__count" id="guides-count"></span></div>
<ul class="az-list" id="guides-list"></ul>
<script>
const hidden = new Set({json.dumps(_hub_hidden_slugs())});
fetch('/guides-index.json')
  .then(r => r.json())
  .then(data => {{
    const entries = Object.keys(data).filter(slug => !hidden.has(slug)).map(slug => ({{ slug, pages: data[slug], name: slug.replace(/-/g, ' ') }}))
      .sort((a, b) => a.name.localeCompare(b.name));
    const list = document.getElementById('guides-list');
    const count = document.getElementById('guides-count');
    const search = document.getElementById('guides-search');
    function render(filter) {{
      const f = (filter || '').toLowerCase().trim();
      const shown = entries.filter(e => e.name.includes(f));
      list.textContent = '';
      shown.slice(0, 600).forEach(e => {{
        const li = document.createElement('li');
        const a = document.createElement('a');
        a.href = e.pages.tips || Object.values(e.pages)[0];
        a.textContent = e.name.replace(/\\b\\w/g, c => c.toUpperCase());
        const s = document.createElement('span');
        s.textContent = Object.keys(e.pages).length + ' guides';
        li.append(a, s);
        list.appendChild(li);
      }});
      count.textContent = shown.length.toLocaleString() + ' games';
    }}
    render('');
    search.addEventListener('input', () => render(search.value));
  }})
  .catch(() => {{
    document.getElementById('guides-list').textContent = 'Guides are being generated - check back soon.';
  }});
</script>"""
    return S.page(head, main, body_attrs='class="guides-hub" data-page="guides"', active="/guides/", total_games=total_games)


# ---------------------------------------------------------------------------
# Writers used by the generator scripts and migrate-ui.py
# ---------------------------------------------------------------------------

MIN_CATEGORY_GAMES = 15


def _write(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def build_category_pages(catalog, write=True, only=None):
    """One page per category with 15+ games (same rule as before); returns {path: html}."""
    position = {g["slug"]: i for i, g in enumerate(catalog)}
    raw_counts = {}
    for g in catalog:
        raw_counts[g.get("category", "")] = raw_counts.get(g.get("category", ""), 0) + 1
    qualifying = [c for c, n in raw_counts.items() if c and n >= MIN_CATEGORY_GAMES]
    pages = {}
    all_categories = []
    for c in qualifying:
        members = [g for g in catalog if g.get("category") == c or
                   (S.primary_category(g.get("category", "")) == c and g.get("category") != c)]
        all_categories.append((c, len(members)))
    all_categories.sort(key=lambda x: -x[1])
    for c, _ in all_categories:
        if only and S.slugify(c) not in only:
            continue
        members = [g for g in catalog if g.get("category") == c or
                   (S.primary_category(g.get("category", "")) == c and g.get("category") != c)]
        members.sort(key=lambda g: (S.source_rank(g), not g.get("thumbnail"), position[g["slug"]]))
        html_doc = render_category_page(c, members, all_categories, total_games=len(catalog), newness=position)
        path = os.path.join(S.ROOT, S.slugify(c), "index.html")
        pages[path] = html_doc
        if write:
            _write(path, html_doc)
    return pages


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def build_home(catalog, write=True):
    featured = _load_json(os.path.join(S.ROOT, "featured.json"), {}).get("games", [])
    popular = _load_json(os.path.join(S.ROOT, "popular-games.json"), {}).get("games", [])
    pages = {
        os.path.join(S.ROOT, "index.html"): render_home(catalog, featured, popular),
        os.path.join(S.ROOT, "all-games", "index.html"): render_all_games(catalog),
    }
    if write:
        for path, html_doc in pages.items():
            _write(path, html_doc)
    return pages


def refresh_game_page(slug, catalog, guides_index, write=True):
    """Re-render one existing game page (e.g. after its guides were generated)."""
    path = os.path.join(S.ROOT, "games", f"{slug}.html")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        doc = f.read()
    fields = extract_game_page(doc, slug)
    if not fields:
        return None
    html_doc = render_game_page(fields, catalog, guides=guides_index.get(slug), total_games=len(catalog))
    if write:
        _write(path, html_doc)
    return html_doc
