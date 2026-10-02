"""Turn parsed document blocks into a deck: a list of slide dictionaries.

Slide layouts produced:
    title     - cover slide                {title, subtitle}
    agenda    - list of sections           {title, items}
    section   - section divider            {title, number, subtitle}
    bullets   - title + bullet points      {title, bullets}
    cards     - "Term: description" items  {title, cards: [{head, body}]}
    stats     - big numbers                {title, stats: [{value, label}]}
    table     - a table                    {title, rows}
    statement - one key sentence           {title, text}
    closing   - thank-you slide            {title, subtitle}
"""

import re

MAX_BULLETS = 5
MAX_WORDS = 24
MAX_TABLE_ROWS = 8
MAX_TABLE_COLS = 6


def build_deck(blocks, fallback_title="Presentation"):
    blocks = [b for b in blocks if b.get("text") or b.get("rows")]
    title, subtitle, blocks = _extract_title(blocks, fallback_title)
    sections = _split_sections(blocks)

    slides = [{"layout": "title", "title": title, "subtitle": subtitle}]

    section_titles = [s["title"] for s in sections if s["title"]]
    if len(section_titles) >= 3:
        slides.append({"layout": "agenda", "title": "Agenda", "items": section_titles[:8]})

    for section in sections:
        # Sections with sub-headings get their own divider slide,
        # numbered to match the agenda.
        if section["title"] and len(section["groups"]) > 1:
            number = section_titles.index(section["title"]) + 1
            lead = section["groups"][0]
            intro = "" if lead["title"] else _first_sentence(lead["content"])
            slides.append({
                "layout": "section", "title": section["title"],
                "number": f"{number:02d}", "subtitle": intro,
            })
            if intro:  # used as subtitle, don't repeat it
                section["groups"][0]["content"] = _drop_first_sentence(section["groups"][0]["content"])
        for group in section["groups"]:
            heading = group["title"] or section["title"] or "Overview"
            slides.extend(_content_slides(heading, group["content"]))

    slides.append({"layout": "closing", "title": "Thank You", "subtitle": "Questions & Discussion"})
    return {"title": title, "slides": slides}


# ------------------------------------------------------------------ structure

def _extract_title(blocks, fallback):
    title, subtitle = fallback, ""
    headings = [b for b in blocks if b["type"] == "heading"]
    if headings:
        top = min(h["level"] for h in headings)
        first = blocks.index(headings[0])
        # Use the first heading as the title if it is the only one at the top level
        # (or a dedicated "Title" style) and it is near the start of the document.
        if first <= 2 and (headings[0]["level"] == 0 or
                           sum(1 for h in headings if h["level"] == top) == 1):
            title = headings[0]["text"]
            rest = blocks[first + 1:]
            if rest and rest[0]["type"] == "paragraph":
                subtitle = _shorten(_sentences(rest[0]["text"])[0], 18)
                rest[0] = dict(rest[0], text=" ".join(_sentences(rest[0]["text"])[1:]))
            return title, subtitle, rest
    return title, subtitle, blocks


def _split_sections(blocks):
    """Group blocks into sections (top heading level) and groups (next level down)."""
    headings = [b["level"] for b in blocks if b["type"] == "heading"]
    if not headings:
        return [{"title": "", "groups": [{"title": "", "content": blocks}]}]
    top = min(headings)

    sections = []
    current = {"title": "", "groups": [{"title": "", "content": []}]}
    for block in blocks:
        if block["type"] == "heading" and block["level"] == top:
            sections.append(current)
            current = {"title": block["text"], "groups": [{"title": "", "content": []}]}
        elif block["type"] == "heading":
            current["groups"].append({"title": block["text"], "content": []})
        else:
            current["groups"][-1]["content"].append(block)
    sections.append(current)

    for s in sections:
        s["groups"] = [g for g in s["groups"] if g["content"]]
    return [s for s in sections if s["groups"] or s["title"]]


# -------------------------------------------------------------- content slides

def _content_slides(heading, content):
    slides, items = [], []

    def flush_items():
        if items:
            slides.extend(_item_slides(heading, items))
            items.clear()

    for block in content:
        if block["type"] == "table":
            flush_items()
            slides.extend(_table_slides(heading, block["rows"]))
        elif block["type"] == "bullet":
            items.append(block["text"])
        elif block["type"] == "paragraph":
            sentences = _sentences(block["text"])
            if not items and len(content) == 1 and len(sentences) == 1 and len(sentences[0].split()) <= 30:
                slides.append({"layout": "statement", "title": heading, "text": sentences[0]})
            else:
                items.extend(sentences)

    flush_items()
    return slides


def _item_slides(heading, items):
    items = [i for i in (s.strip() for s in items) if len(i) > 2]
    if not items:
        return []

    pairs = [_split_term(i) for i in items]
    if 2 <= len(items) <= 6 and all(pairs):
        return [{"layout": "cards", "title": heading,
                 "cards": [{"head": h, "body": _capitalize(_shorten(b, 22))} for h, b in pairs]}]

    stats = [_split_stat(i) for i in items]
    if 2 <= len(items) <= 4 and all(stats):
        return [{"layout": "stats", "title": heading,
                 "stats": [{"value": v, "label": _shorten(l, 14)} for v, l in stats]}]

    bullets = [_shorten(i, MAX_WORDS) for i in items]
    chunks = _chunk(bullets, MAX_BULLETS)
    slides = []
    for n, chunk in enumerate(chunks):
        t = heading if n == 0 else f"{heading} (cont.)"
        slides.append({"layout": "bullets", "title": t, "bullets": chunk})
    return slides


def _table_slides(heading, rows):
    rows = [r[:MAX_TABLE_COLS] for r in rows]
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, body = rows[0], rows[1:]
    slides = []
    for n, chunk in enumerate(_chunk(body, MAX_TABLE_ROWS - 1) or [[]]):
        t = heading if n == 0 else f"{heading} (cont.)"
        slides.append({"layout": "table", "title": t, "rows": [header] + chunk})
    return slides


# --------------------------------------------------------------------- helpers

def _split_term(text):
    m = re.match(r"^([^:–—]{2,40})\s*[:–—]\s+(.{8,})$", text)
    return (m.group(1).strip(), m.group(2).strip()) if m else None


def _split_stat(text):
    m = re.match(r"^\s*([$€£]?\d[\d,.]*\s?(%|[kKmMbB]\b|x\b|\+)?)\s+(.{3,})$", text)
    if m:
        return m.group(1).strip(), m.group(3).strip(" -–:")
    return None


def _capitalize(text):
    return text[:1].upper() + text[1:]


def _sentences(text):
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _first_sentence(content):
    if content and content[0]["type"] == "paragraph":
        return _shorten(_sentences(content[0]["text"])[0], 20)
    return ""


def _drop_first_sentence(content):
    rest = " ".join(_sentences(content[0]["text"])[1:])
    return ([dict(content[0], text=rest)] if rest else []) + content[1:]


def _shorten(text, max_words):
    words = text.split()
    if len(words) <= max_words:
        return text
    cut = " ".join(words[:max_words])
    # prefer ending at a clause boundary
    m = re.match(r"^(.{20,}[,;:])\s", cut + " ")
    return (m.group(1)[:-1] if m else cut).rstrip(",;:") + "…"


def _chunk(items, size):
    if len(items) <= size:
        return [items] if items else []
    n = -(-len(items) // size)            # ceil: spread evenly over n slides
    per = -(-len(items) // n)
    return [items[i:i + per] for i in range(0, len(items), per)]
