"""Generate a whole deck from a text prompt with Claude.

Claude returns a structured outline (validated against the Pydantic models
below) that uses the same layouts the renderer already knows how to draw.

Two ways to reach Claude:
- the Claude Code command-line tool, using the user's existing Claude login (no API key);
- the Anthropic API with an API key (used when a key is set).
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import List, Literal

import anthropic
from pydantic import BaseModel

MODEL = "claude-opus-5-5"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class AIError(Exception):
    """A problem worth showing to the user as-is."""


# ------------------------------------------------------------------ schema

class Card(BaseModel):
    head: str
    body: str


class Stat(BaseModel):
    value: str
    label: str


class AISlide(BaseModel):
    layout: Literal["section", "bullets", "cards", "stats", "table", "statement"]
    title: str
    subtitle: str
    bullets: List[str]
    cards: List[Card]
    stats: List[Stat]
    table: List[List[str]]
    text: str
    notes: str


class AIDeck(BaseModel):
    title: str
    subtitle: str
    agenda_title: str
    closing_title: str
    closing_subtitle: str
    slides: List[AISlide]


SYSTEM = """You design presentation decks. You write the content; a renderer applies the visual design.

Each slide uses exactly one layout. Fill only the fields that layout uses and leave the others empty ("" or []):
- section: a divider that opens a group of slides. Uses title and subtitle (one short sentence).
- bullets: title and 3-5 bullets of at most 16 words each.
- cards: title and 2-6 cards. head is 1-4 words, body is at most 20 words.
- stats: title and 2-4 stats. value is short ("32%", "5,000", "3x", "$1.2M"); label is at most 8 words.
- table: title and a table of at most 6 rows (the first row is the header) and at most 5 columns.
- statement: title and text, one memorable sentence of at most 25 words.

Every slide gets notes: 1-2 sentences the presenter can say aloud, adding detail that is not on the slide.

Write a deck with a clear story: open with the context or problem, build through the main points, and end with conclusions or next steps. Vary the layouts so consecutive slides don't look alike, and choose the layout that suits the content (numbers become stats, comparisons become tables, parallel concepts become cards). Use section slides only for decks of 8 or more slides, to group related slides. Use only figures you are confident are accurate; when you are not sure of a number, describe the point without one.

Do not include a title slide, an agenda or a closing slide; those are added automatically from title, subtitle, agenda_title, closing_title and closing_subtitle. Write everything, including agenda_title and the closing text, in the language of the user's request."""


# ------------------------------------------------------------------- API key

def _env_key():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "ANTHROPIC_API_KEY" and value.strip():
                return value.strip().strip('"')
    return None


def has_api_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or _env_key())


def save_api_key(key):
    """Store the key in the project's .env file (kept out of git by .gitignore)."""
    lines = []
    if ENV_FILE.exists():
        lines = [l for l in ENV_FILE.read_text(encoding="utf-8").splitlines()
                 if l.partition("=")[0].strip() != "ANTHROPIC_API_KEY"]
    lines.append(f"ANTHROPIC_API_KEY={key.strip()}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _client():
    key = os.environ.get("ANTHROPIC_API_KEY") or _env_key()
    if not key:
        raise AIError("No Anthropic API key is set. Paste your key in the 🔑 API key box first.")
    return anthropic.Anthropic(api_key=key)


# ------------------------------------------------- Claude Code login (no key)

def find_claude_cli():
    """Path of the Claude Code executable, which uses the user's Claude login."""
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    candidates = [
        appdata / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe",
        home / ".local" / "bin" / "claude.exe",
        home / ".local" / "bin" / "claude",
    ]
    for path in candidates:
        if path.is_file():
            return path
    found = shutil.which("claude")
    # .cmd/.ps1 wrappers re-parse arguments through a shell, so only use a real executable
    return Path(found) if found and not found.lower().endswith((".cmd", ".ps1", ".bat")) else None


def has_claude():
    """Claude mode is available with either the Claude Code login or an API key."""
    return bool(find_claude_cli() or has_api_key())


def _inline_refs(schema):
    """--json-schema gets a self-contained schema: replace Pydantic's $ref/$defs."""
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node
    return walk(schema)


_SLIDE_RE = re.compile(r'"layout"\s*:\s*"(\w+)"\s*,\s*"title"\s*:\s*"((?:[^"\\]|\\.)*)"')
_TITLE_RE = re.compile(r'^\s*\{\s*"title"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _progress_from_partial(partial):
    """Deck title and slide titles found so far in the half-written JSON."""
    def unescape(s):
        try:
            return json.loads(f'"{s}"')
        except ValueError:
            return s
    m = _TITLE_RE.match(partial)
    slides = [(layout, unescape(t)) for layout, t in _SLIDE_RE.findall(partial)]
    return {"title": unescape(m.group(1)) if m else "", "slides": slides}


def _generate_with_cli(exe, request, progress=None):
    """Run Claude Code with streaming output, reporting each slide as it's written."""
    schema = json.dumps(_inline_refs(AIDeck.model_json_schema()))
    cmd = [str(exe), "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--model", "opus", "--effort", "low",   # low effort measured fastest (~25% quicker)
           "--tools", "", "--no-session-persistence",
           "--system-prompt", SYSTEM, "--json-schema", schema]
    # stderr goes to a file: a full stderr pipe would block the process while we read stdout
    errors = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
    try:
        # run outside the project so its settings and files don't affect the answer
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=errors, text=True, encoding="utf-8",
                                cwd=tempfile.gettempdir())
    except OSError as exc:
        errors.close()
        raise AIError(f"Could not start Claude Code: {exc}")

    timer = threading.Timer(600, proc.kill)  # hard stop after 10 minutes
    timer.start()
    result, partial, other = None, "", []
    try:
        proc.stdin.write(request)
        proc.stdin.close()
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                other.append(line)
                continue
            if event.get("type") == "result":
                result = event
            elif event.get("type") == "stream_event":
                delta = event["event"].get("delta", {})
                if delta.get("type") == "input_json_delta":
                    partial += delta.get("partial_json", "")
                    if progress:
                        progress(_progress_from_partial(partial))
        proc.wait()
        errors.seek(0)
        stderr = errors.read()
    finally:
        timer.cancel()
        errors.close()

    if result is None:
        detail = (stderr + "".join(other)).strip().splitlines()
        if any("login" in l.lower() or "auth" in l.lower() for l in detail):
            raise AIError("Claude Code is not signed in. Open a terminal, run “claude”, "
                          "sign in once, then try again.")
        if proc.returncode and proc.returncode < 0 or not detail:
            raise AIError("Claude took too long or stopped. Please try again.")
        raise AIError("Claude Code returned an error: " + detail[-1])

    if result.get("is_error") or not result.get("structured_output"):
        message = str(result.get("result") or result.get("subtype") or "unknown error")
        if "limit" in message.lower():
            raise AIError("Your Claude usage limit has been reached. Try again later. " + message[:200])
        raise AIError("Claude could not write this presentation: " + message[:300])
    try:
        return AIDeck.model_validate(result["structured_output"])
    except ValueError:
        raise AIError("Claude's answer was incomplete. Please try again.")


# ---------------------------------------------------------------- generation

def generate_deck(prompt, n_slides=10, audience="", tone="", progress=None):
    """progress(info) is called with {"title", "slides": [(layout, title), ...]} while writing."""
    request = [f"Create a presentation: {prompt.strip()}",
               f"Number of slides: {n_slides}, counting section slides "
               "(the title, agenda and closing slides are extra)."]
    if audience.strip():
        request.append(f"Audience: {audience.strip()}")
    if tone.strip():
        request.append(f"Tone: {tone.strip()}")

    # prefer the Claude Code login (no API key needed); fall back to an API key
    exe = find_claude_cli()
    if exe and not has_api_key():
        return to_deck(_generate_with_cli(exe, "\n".join(request), progress))

    try:
        response = _client().beta.messages.parse(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": "\n".join(request)}],
            output_format=AIDeck,
            output_config={"effort": "low"},  # measured fastest with no loss in deck structure
            # If a safety classifier declines, the API retries on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        raise AIError("The Anthropic API key was rejected. Check it in the 🔑 API key box.")
    except anthropic.PermissionDeniedError:
        raise AIError("This API key is not allowed to use the model. Check your Anthropic account.")
    except anthropic.RateLimitError:
        raise AIError("Too many requests right now. Wait a minute and try again.")
    except anthropic.APIConnectionError:
        raise AIError("Could not reach the Anthropic API. Check your internet connection.")
    except anthropic.APIStatusError as exc:
        raise AIError(f"The AI service returned an error ({exc.status_code}): {exc.message}")

    if response.stop_reason == "refusal":
        raise AIError("The AI declined to write this presentation. Try rephrasing the prompt.")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise AIError("The presentation was too long to finish. Ask for fewer slides.")
    return to_deck(response.parsed_output)


def to_deck(ai):
    """Convert Claude's outline into the deck format used by the renderer."""
    slides = [{"layout": "title", "title": ai.title, "subtitle": ai.subtitle}]
    sections = [s.title for s in ai.slides if s.layout == "section"]
    if len(sections) >= 3:
        slides.append({"layout": "agenda", "title": ai.agenda_title or "Agenda", "items": sections})

    number = 0
    for s in ai.slides:
        d = {"layout": s.layout, "title": s.title, "notes": s.notes}
        if s.layout == "section":
            number += 1
            d.update(number=f"{number:02d}", subtitle=s.subtitle)
        elif s.layout == "cards" and s.cards:
            d["cards"] = [c.model_dump() for c in s.cards[:6]]
        elif s.layout == "stats" and s.stats:
            d["stats"] = [st.model_dump() for st in s.stats[:4]]
        elif s.layout == "table" and len(s.table) >= 2:
            width = max(len(r) for r in s.table[:7])
            d["rows"] = [(r + [""] * width)[:min(width, 6)] for r in s.table[:7]]
        elif s.layout == "statement" and s.text:
            d["text"] = s.text
        else:  # bullets, or a layout whose fields came back empty
            bullets = s.bullets or [c.head + ": " + c.body for c in s.cards] or \
                      [st.value + " " + st.label for st in s.stats] or [s.text or s.subtitle]
            d.update(layout="bullets", bullets=[b for b in bullets if b][:6])
        slides.append(d)

    slides.append({"layout": "closing", "title": ai.closing_title or "Thank You",
                   "subtitle": ai.closing_subtitle or "Questions & Discussion"})
    return {"title": ai.title, "slides": slides, "source": "AI prompt"}
