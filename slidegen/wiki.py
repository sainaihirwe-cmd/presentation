"""Free prompt-to-deck mode: research the topic on Wikipedia (no API key needed).

The article is turned into the same heading/paragraph blocks a document produces,
so builder.build_deck lays it out exactly like an uploaded document.
"""

import json
import re
import urllib.parse
import urllib.request

from .builder import build_deck

USER_AGENT = "SlideGen/1.0 (local presentation maker; python urllib)"

LANGUAGES = {"en": "English", "fr": "Français", "rw": "Kinyarwanda", "sw": "Kiswahili"}

# text used for the agenda and closing slides in each language
WORDS = {
    "en": ("Agenda", "Thank You", "Questions & Discussion", "Overview", "Source"),
    "fr": ("Sommaire", "Merci", "Questions et discussion", "Aperçu", "Source"),
    "rw": ("Ibikubiyemo", "Murakoze", "Ibibazo n'ibiganiro", "Incamake", "Inkomoko"),
    "sw": ("Ajenda", "Asante", "Maswali na majadiliano", "Muhtasari", "Chanzo"),
}

# sections that are lists of links or references rather than content
SKIP_SECTIONS = re.compile(
    r"^(see also|references|external links|notes|further reading|bibliography|sources|"
    r"citations|footnotes|gallery|voir aussi|références|liens externes|notes et références|"
    r"bibliographie|annexes|marejeo|viungo vya nje|tazama pia|reba kandi|indanganturo)$", re.I)


class WikiError(Exception):
    """A problem worth showing to the user as-is."""


def _get(lang, **params):
    params.update(format="json", formatversion="2")
    url = f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)
    except OSError:
        raise WikiError("Could not reach Wikipedia. Check your internet connection and try again.")


def find_article(topic, lang="en"):
    """Return the title of the best-matching article for a free-text topic."""
    # key words first: long prompts like "solar energy for students" match the wrong article
    for query in dict.fromkeys((_keywords(topic), topic)):
        if not query:
            continue
        hits = _get(lang, action="query", list="search", srsearch=query, srlimit=1)
        results = hits.get("query", {}).get("search", [])
        if results:
            return results[0]["title"]
    raise WikiError(f"Wikipedia has no article matching “{topic}”. Try a shorter topic, "
                    "for example “Solar energy” instead of a full sentence.")


def _keywords(text):
    """Drop filler words so long prompts still find an article."""
    filler = r"\b(a|an|the|about|for|on|of|to|and|presentation|slides?|deck|talk|make|create|" \
             r"me|my|please|students?|high[- ]school|kids|children|beginners|introduction|intro)\b"
    return re.sub(r"\s+", " ", re.sub(filler, " ", text, flags=re.I)).strip()


def fetch_article(title, lang="en"):
    data = _get(lang, action="query", prop="extracts|pageimages|info", titles=title,
                explaintext=1, exsectionformat="wiki", redirects=1,
                piprop="thumbnail", pithumbsize=1600, inprop="url")
    page = data["query"]["pages"][0]
    if page.get("missing") or not page.get("extract"):
        raise WikiError(f"Could not read the Wikipedia article “{title}”.")
    return {
        "title": page["title"],
        "text": page["extract"],
        "url": page.get("fullurl", f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(title)),
        "image": (page.get("thumbnail") or {}).get("source"),
    }


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


# --------------------------------------------------------------- text -> blocks

def _clean(text):
    text = re.sub(r"\s*\([^()]*\)", "", text)          # parentheses: pronunciations, dates, asides
    text = re.sub(r"\s*\([^()]*\)", "", text)          # (second pass for nested ones)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _sentences(text, limit):
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9À-Ý\"“])", text)
    # skip sentences that end in ":" — they introduce a list we don't show
    parts = [p for p in parts if len(p.split()) >= 4 and not p.rstrip().endswith(":")]
    return " ".join(parts[:limit])


def article_sections(text):
    """Split an article into (level, heading, paragraphs). The lead section has level 0."""
    sections, current = [], [0, "", []]
    for line in text.splitlines():
        m = re.match(r"^(={2,6})\s*(.*?)\s*\1\s*$", line.strip())
        if m:
            sections.append(current)
            current = [len(m.group(1)) - 1, m.group(2), []]
        elif line.strip():
            current[2].append(line.strip())
    sections.append(current)
    return sections


def to_blocks(article, max_sections):
    """Title + lead, then one slide's worth of content per top-level section.

    A section with sub-sections becomes "Sub-heading: first sentence" items, which
    the builder lays out as cards; otherwise its first sentences become bullets.
    """
    sections = article_sections(article["text"])
    blocks = [{"type": "heading", "level": 0, "text": article["title"]}]
    lead = _sentences(_clean(" ".join(sections[0][2][:2])), 5)
    if lead:
        blocks.append({"type": "paragraph", "text": lead})

    # group sub-sections under their top-level section
    groups = []
    for level, heading, paras in sections[1:]:
        text = _clean(" ".join(paras[:2]))
        if level == 1:
            groups.append({"heading": heading, "text": text, "subs": []})
        elif level == 2 and groups and text:
            groups[-1]["subs"].append((heading.replace(":", " ").strip(), text))

    kept = 0
    for g in groups:
        if kept >= max_sections or SKIP_SECTIONS.match(g["heading"].strip()):
            continue
        if len(g["subs"]) >= 2:
            items = [f"{h}: {_sentences(t, 1)}" for h, t in g["subs"][:6] if _sentences(t, 1)]
            blocks.append({"type": "heading", "level": 1, "text": g["heading"]})
            blocks.extend({"type": "bullet", "text": i} for i in items)
        else:
            text = g["text"] or (g["subs"][0][1] if g["subs"] else "")
            text = _sentences(text, 4)
            if not text:
                continue
            blocks.append({"type": "heading", "level": 1, "text": g["heading"]})
            blocks.append({"type": "paragraph", "text": text})
        kept += 1
    return blocks


def generate_deck(topic, n_slides=10, lang="en"):
    """Research `topic` on Wikipedia and return (deck, cover_image_url)."""
    lang = lang if lang in LANGUAGES else "en"
    article = fetch_article(find_article(topic, lang), lang)

    # the overview takes one slide, each section one more
    deck = build_deck(to_blocks(article, max(1, n_slides - 1)), fallback_title=article["title"])
    extra = [i for i, s in enumerate(deck["slides"]) if s["title"].endswith("(cont.)")]
    if len(deck["slides"]) - len(extra) - 3 > n_slides:  # drop "(cont.)" spill-over slides
        deck["slides"] = [s for i, s in enumerate(deck["slides"]) if i not in extra]

    agenda, thanks, questions, overview, source = WORDS[lang]
    for s in deck["slides"]:
        if s["layout"] == "agenda":
            s["title"] = agenda
        elif s["layout"] == "closing":
            s.update(title=thanks, subtitle=f"{questions} · {source}: Wikipedia")
        elif s["title"] == "Overview":
            s["title"] = overview
    deck["source"] = f"Wikipedia: {article['title']}"
    deck["source_url"] = article["url"]
    return deck, article["image"]
