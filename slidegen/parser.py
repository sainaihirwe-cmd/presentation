"""Read a document and turn it into a list of blocks.

Every supported format is normalised into the same simple structure:

    [{"type": "heading", "level": 1, "text": "..."},
     {"type": "paragraph", "text": "..."},
     {"type": "bullet", "text": "..."},
     {"type": "table", "rows": [["a", "b"], ["c", "d"]]}]
"""

import re
from pathlib import Path

SUPPORTED = {".docx", ".pdf", ".md", ".markdown", ".txt"}


def parse_document(path):
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".docx":
        return _parse_docx(path)
    if ext == ".pdf":
        return _parse_pdf(path)
    if ext in (".md", ".markdown"):
        return _parse_markdown(path.read_text(encoding="utf-8-sig", errors="ignore"))
    if ext == ".txt":
        return _parse_plain(path.read_text(encoding="utf-8-sig", errors="ignore"))
    raise ValueError(f"Unsupported file type: {ext}")


# ---------------------------------------------------------------- Word (.docx)

def _parse_docx(path):
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(str(path))
    blocks = []

    # Walk the body in order so tables stay where they were in the document.
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, document)
            text = para.text.strip()
            if not text:
                continue
            style = (para.style.name or "").lower() if para.style is not None else ""
            if style == "title":
                blocks.append({"type": "heading", "level": 0, "text": text})
            elif style.startswith("heading"):
                level = int(re.sub(r"\D", "", style) or 1)
                blocks.append({"type": "heading", "level": level, "text": text})
            elif "list" in style or child.find(".//{*}numPr") is not None:
                blocks.append({"type": "bullet", "text": text})
            elif _looks_like_heading(para, text):
                blocks.append({"type": "heading", "level": 2, "text": text})
            else:
                blocks.append({"type": "paragraph", "text": text})
        elif tag == "tbl":
            table = Table(child, document)
            rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
            rows = [r for r in rows if any(r)]
            if rows:
                blocks.append({"type": "table", "rows": rows})
    return blocks


def _looks_like_heading(para, text):
    """Short, fully bold lines are treated as headings (common in informal docs)."""
    runs = [r for r in para.runs if r.text.strip()]
    return (
        len(text) < 80
        and not text.endswith(".")
        and runs
        and all(r.bold for r in runs)
    )


# ---------------------------------------------------------------------- PDF

def _parse_pdf(path):
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    return _parse_plain(text)


# ----------------------------------------------------------------- Markdown

def _parse_markdown(text):
    blocks, para, table = [], [], []

    def flush():
        if para:
            blocks.append({"type": "paragraph", "text": _strip_md(" ".join(para))})
            para.clear()
        if table:
            rows = [r for r in table if not re.fullmatch(r"[\s|:-]+", "|".join(r))]
            if rows:
                blocks.append({"type": "table", "rows": rows})
            table.clear()

    in_code = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            in_code = not in_code
            flush()
            continue
        if in_code:
            continue
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)", stripped)
        bullet = re.match(r"^([-*+]|\d+[.)])\s+(.*)", stripped)
        if heading:
            flush()
            level = len(heading.group(1))
            blocks.append({"type": "heading", "level": level, "text": _strip_md(heading.group(2))})
        elif stripped.startswith("|"):
            if para:
                flush()
            table.append([_strip_md(c.strip()) for c in stripped.strip("|").split("|")])
        elif bullet:
            flush()
            blocks.append({"type": "bullet", "text": _strip_md(bullet.group(2))})
        elif stripped.startswith(">"):
            flush()
            blocks.append({"type": "paragraph", "text": _strip_md(stripped.lstrip("> "))})
        else:
            if table:
                flush()
            para.append(stripped)
    flush()
    return blocks


def _strip_md(text):
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)          # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)       # links
    text = re.sub(r"(\*\*|__|\*|_|`)(.+?)\1", r"\2", text)     # emphasis / code
    return text.strip()


# --------------------------------------------------------------- Plain text

def _parse_plain(text):
    """Heuristic structure detection for text and PDFs."""
    blocks, para = [], []

    def flush():
        if para:
            blocks.append({"type": "paragraph", "text": " ".join(para)})
            para.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        bullet = re.match(r"^([-*•▪●◦–]|\d+[.)])\s+(.*)", line)
        numbered_heading = re.match(r"^(\d+(\.\d+)*)\.?\s+([A-Z].{2,70})$", line)
        if bullet and not numbered_heading:
            flush()
            blocks.append({"type": "bullet", "text": bullet.group(2)})
        elif numbered_heading and not line.endswith("."):
            flush()
            level = numbered_heading.group(1).count(".") + 1
            blocks.append({"type": "heading", "level": level, "text": numbered_heading.group(3)})
        elif _plain_heading(line):
            flush()
            blocks.append({"type": "heading", "level": 1 if line.isupper() else 2, "text": line.title() if line.isupper() else line})
        else:
            para.append(line)
    flush()
    return blocks


def _plain_heading(line):
    words = line.split()
    if not (1 <= len(words) <= 9) or line[-1] in ".,;:!?" or len(line) > 70:
        return False
    if line.isupper() and any(c.isalpha() for c in line):
        return True
    # Title Case lines: most words capitalised
    caps = sum(1 for w in words if w[0].isupper())
    return len(words) >= 2 and caps / len(words) >= 0.75
