"""SlideGen web app: turn a document or a prompt into a designed presentation.

Run locally:  python app.py   then open http://127.0.0.1:5000
On a server:  gunicorn app:app   (see render.yaml)

Server settings (environment variables):
  SECRET_KEY         signs session cookies; set a long random value on a server
  ANTHROPIC_API_KEY  enables Claude mode on a server (no Claude Code login there)
  SITE_PASSWORD      optional; if set, visitors must log in with it (protects your API key)
  ALLOW_KEY_FORM     "1" to allow saving an API key from the web page (on by default locally)
"""

import hmac
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   send_file, url_for)
from werkzeug.utils import secure_filename

from slidegen import THEMES, build_deck, parse_document, render_pptx, wiki
from slidegen.ai import (AIError, find_claude_cli, generate_deck, has_api_key, has_claude,
                         save_api_key)
from slidegen.media import AUDIO_TYPES, IMAGE_TYPES, VIDEO_TYPES, save_image, youtube_id
from slidegen.parser import SUPPORTED
from slidegen.themes import DEFAULT_THEME

BASE = Path(__file__).parent
UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"
MEDIA = OUTPUTS / "media"
UPLOADS.mkdir(exist_ok=True)
MEDIA.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "slidegen-local")
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024  # 300 MB, room for videos

# Render (and most hosts) set these; a public server must not let visitors save an API key
ON_SERVER = bool(os.environ.get("RENDER") or os.environ.get("PORT"))
ALLOW_KEY_FORM = os.environ.get("ALLOW_KEY_FORM", "0" if ON_SERVER else "1") == "1"
SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "")


@app.before_request
def require_password():
    """Optional site-wide password (HTTP basic auth) so strangers can't spend your API key."""
    if not SITE_PASSWORD:
        return None
    auth = request.authorization
    if auth and hmac.compare_digest(auth.password or "", SITE_PASSWORD):
        return None
    return app.response_class("Password required.", 401,
                              {"WWW-Authenticate": 'Basic realm="SlideGen"'})


TEXT_FIELDS = {"title", "subtitle", "text", "caption", "notes"}
LOCKED_FIELDS = {"layout", "style", "image", "file", "audio", "url", "youtube", "number"}


def _types(exts):
    return ", ".join(sorted(exts))


# ------------------------------------------------------------------ helpers

def _new_id():
    return uuid.uuid4().hex[:10]


def _load(deck_id):
    if not re.fullmatch(r"[0-9a-f]{10}", deck_id):
        abort(404)
    path = OUTPUTS / f"{deck_id}.json"
    if not path.exists():
        abort(404)
    return json.loads(path.read_text(encoding="utf-8"))


def _save(deck_id, deck):
    (OUTPUTS / f"{deck_id}.json").write_text(json.dumps(deck, indent=2), encoding="utf-8")


def _theme_arg():
    theme = request.values.get("theme", DEFAULT_THEME)
    return theme if theme in THEMES else DEFAULT_THEME


def _back(deck_id):
    return redirect(url_for("view_deck", deck_id=deck_id, theme=_theme_arg()))


def _media_dir(deck_id):
    folder = MEDIA / deck_id
    folder.mkdir(exist_ok=True)
    return folder


def _delete_media(deck_id, name):
    if name:
        (MEDIA / deck_id / secure_filename(name)).unlink(missing_ok=True)


def _save_upload(deck_id, file, allowed):
    """Save an uploaded media file; returns its stored name or None if the type is wrong."""
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed:
        return None
    stem = uuid.uuid4().hex[:8]
    if allowed is IMAGE_TYPES:
        return save_image(file, _media_dir(deck_id), stem)
    file.save(_media_dir(deck_id) / (stem + ext))
    return stem + ext


# --------------------------------------------------------------------- home

@app.get("/")
def index():
    return render_template("index.html", themes=THEMES, default=DEFAULT_THEME,
                           formats=_types(SUPPORTED), has_key=has_api_key(),
                           has_cli=bool(find_claude_cli()), has_claude=has_claude(),
                           tab=request.args.get("tab", "document"), languages=wiki.LANGUAGES,
                           allow_key_form=ALLOW_KEY_FORM)


@app.post("/convert")
def convert():
    file = request.files.get("document")
    theme = _theme_arg()
    if not file or not file.filename:
        flash("Please choose a document to upload.")
        return redirect(url_for("index"))

    name = secure_filename(file.filename) or "document"
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED:
        flash(f"Unsupported file type '{ext}'. Use one of: {_types(SUPPORTED)}")
        return redirect(url_for("index"))

    deck_id = _new_id()
    src = UPLOADS / f"{deck_id}{ext}"
    file.save(src)

    try:
        blocks = parse_document(src)
    except Exception as exc:  # corrupt / encrypted files etc.
        flash(f"Could not read the document: {exc}")
        return redirect(url_for("index"))
    if not blocks:
        flash("No text was found in that document (scanned PDFs are not supported).")
        return redirect(url_for("index"))

    fallback = re.sub(r"[_-]+", " ", Path(name).stem).title()
    deck = build_deck(blocks, fallback_title=fallback)
    deck["source"] = file.filename
    _save(deck_id, deck)
    return redirect(url_for("view_deck", deck_id=deck_id, theme=theme))


@app.post("/create")
def create():
    """Generate a deck from a text prompt: free Wikipedia research, or Claude with an API key."""
    prompt = request.form.get("prompt", "").strip()
    if len(prompt) < 2:
        flash("Type a topic, for example “Solar energy” or “Photosynthesis”.")
        return redirect(url_for("index", tab="prompt"))
    try:
        n = max(3, min(int(request.form.get("slides", 10)), 25))
    except ValueError:
        n = 10

    if request.form.get("engine") == "claude":
        # Claude takes ~20-40 s: run it in the background and show live progress meanwhile
        job_id = _new_id()
        JOBS[job_id] = {"state": "running", "started": time.time(), "prompt": prompt,
                        "n": n, "title": "", "slides": [], "theme": _theme_arg()}
        threading.Thread(target=_run_claude_job, daemon=True,
                         args=(job_id, prompt, n, request.form.get("audience", ""),
                               request.form.get("tone", ""))).start()
        return redirect(url_for("job_page", job_id=job_id))

    deck_id, cover_url = _new_id(), None
    try:
        deck, cover_url = wiki.generate_deck(prompt, n, request.form.get("lang", "en"))
    except wiki.WikiError as exc:
        flash(str(exc))
        return redirect(url_for("index", tab="prompt"))

    if cover_url:  # the article's main photo becomes the cover; skip it if it can't be used
        try:
            deck["cover"] = save_image(wiki.download(cover_url), _media_dir(deck_id), "cover")
        except Exception:
            pass
    deck["prompt"] = prompt
    _save(deck_id, deck)
    return redirect(url_for("view_deck", deck_id=deck_id, theme=_theme_arg()))


JOBS = {}  # job id -> progress of a Claude deck being written (kept in memory)


def _run_claude_job(job_id, prompt, n, audience, tone):
    job = JOBS[job_id]

    def progress(info):
        job["title"], job["slides"] = info["title"], info["slides"]

    try:
        deck = generate_deck(prompt, n, audience, tone, progress=progress)
    except AIError as exc:
        job.update(state="error", error=str(exc))
        return
    except Exception as exc:  # never leave the progress page waiting forever
        job.update(state="error", error=f"Something went wrong: {exc}")
        return
    deck["prompt"] = prompt
    deck_id = _new_id()
    _save(deck_id, deck)
    job.update(state="done", deck_id=deck_id, seconds=round(time.time() - job["started"]))


@app.get("/job/<job_id>")
def job_page(job_id):
    job = JOBS.get(job_id) or abort(404)
    return render_template("progress.html", job=job, job_id=job_id)


@app.get("/job/<job_id>/status")
def job_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(state="error", error="This job was lost (the server restarted). Please try again.")
    data = {k: job.get(k) for k in ("state", "title", "n", "error")}
    data["slides"] = [{"layout": l, "title": t} for l, t in job["slides"]]
    data["elapsed"] = round(time.time() - job["started"])
    if job["state"] == "done":
        data["url"] = url_for("view_deck", deck_id=job["deck_id"], theme=job["theme"])
    return jsonify(data)


@app.post("/settings/key")
def set_key():
    if not ALLOW_KEY_FORM:
        abort(403)  # on a public server the key is set as an environment variable instead
    key = request.form.get("api_key", "")
    # tolerate what often gets copied along with the key
    key = re.sub(r"^\s*(ANTHROPIC_API_KEY\s*=|Bearer\s+)", "", key, flags=re.I)
    key = re.sub(r"\s+", "", key).strip("\"'`")

    if not key.startswith("sk-ant-"):
        if key.startswith(("sk-proj-", "sk-")):
            kind = "an OpenAI (ChatGPT) key"
        elif key.startswith("AIza"):
            kind = "a Google (Gemini) key"
        elif key.startswith(("gsk_", "hf_", "xai-")):
            kind = "a key from another AI service"
        else:
            kind = None
        # show only a tiny hint of what arrived, so the user can tell what got pasted
        seen = f"it starts with “{key[:3]}…” and has {len(key)} characters" if key else "the box was empty"
        what = f"That looks like {kind}" if kind else "That doesn't look like an API key"
        flash(f"{what} ({seen}). An Anthropic key starts with sk-ant- "
              "and is about 100 characters long; click 👁 to check what is in the box. "
              "You don't need a key at all for 🌍 Free mode, which is already selected above.")
        return redirect(url_for("index", tab="prompt"))
    else:
        save_api_key(key)
        flash("API key saved. You can now create presentations from a prompt.")
    return redirect(url_for("index", tab="prompt"))


# --------------------------------------------------------------------- deck

@app.get("/deck/<deck_id>")
def view_deck(deck_id):
    deck = _load(deck_id)
    theme = _theme_arg()
    return render_template("deck.html", deck=deck, deck_id=deck_id, theme_key=theme,
                           theme=THEMES[theme], themes=THEMES,
                           video_types=_types(VIDEO_TYPES), image_types=_types(IMAGE_TYPES),
                           audio_types=_types(AUDIO_TYPES))


@app.post("/deck/<deck_id>/add")
def add_media(deck_id):
    """Add a video, photo or audio from the "Add media" dialog."""
    deck = _load(deck_id)
    slides = deck["slides"]
    kind = request.form.get("kind")
    file = request.files.get("file")
    has_file = bool(file and file.filename)
    title = request.form.get("title", "").strip()
    text = request.form.get("text", "").strip()

    # default position: just before the closing "Thank You" slide
    default = len(slides) - 1 if slides and slides[-1]["layout"] == "closing" else len(slides)
    try:
        pos = int(request.form.get("after", default))
    except ValueError:
        pos = default
    pos = max(1, min(pos, len(slides)))

    if kind == "video":
        slide = {"layout": "video", "title": title or "Video", "caption": text}
        link = request.form.get("url", "").strip()
        if has_file:
            slide["file"] = _save_upload(deck_id, file, VIDEO_TYPES)
            if not slide["file"]:
                flash(f"Unsupported video type. Use one of: {_types(VIDEO_TYPES)}")
                return _back(deck_id)
        elif link:
            link = link if re.match(r"^https?://", link) else "https://" + link
            slide.update(url=link, youtube=youtube_id(link))
        else:
            flash("Choose a video file or paste a video link.")
            return _back(deck_id)
        slides.insert(pos, slide)
        flash(f"Video slide “{slide['title']}” added.")

    elif kind == "photo":
        if not has_file:
            flash("Choose a photo to upload.")
            return _back(deck_id)
        name = _save_upload(deck_id, file, IMAGE_TYPES)
        if not name:
            flash(f"Unsupported image type. Use one of: {_types(IMAGE_TYPES)}")
            return _back(deck_id)
        style = request.form.get("style", "full")
        if style == "cover":
            _delete_media(deck_id, deck.get("cover"))
            deck["cover"] = name
            flash("Cover photo set on the title slide.")
        else:
            slides.insert(pos, {"layout": "photo", "style": style if style in
                                ("full", "left", "right", "framed") else "full",
                                "image": name, "title": title or "Photo", "text": text})
            flash(f"Photo slide “{title or 'Photo'}” added.")

    elif kind == "audio":
        if not has_file:
            flash("Choose an audio file to upload.")
            return _back(deck_id)
        name = _save_upload(deck_id, file, AUDIO_TYPES)
        if not name:
            flash(f"Unsupported audio type. Use one of: {_types(AUDIO_TYPES)}")
            return _back(deck_id)
        target = request.form.get("target", "music")
        if target == "music":
            _delete_media(deck_id, deck.get("music"))
            deck["music"] = name
            flash("Background music added to the presentation.")
        else:
            try:
                slide = slides[int(target)]
            except (ValueError, IndexError):
                abort(400)
            _delete_media(deck_id, slide.get("audio"))
            slide["audio"] = name
            flash(f"Audio added to slide {int(target) + 1}.")
    else:
        abort(400)

    _save(deck_id, deck)
    return _back(deck_id)


@app.post("/deck/<deck_id>/remove-media")
def remove_media(deck_id):
    """Remove the cover photo, the background music or one slide's audio."""
    deck = _load(deck_id)
    what = request.form.get("what")
    if what in ("cover", "music"):
        _delete_media(deck_id, deck.pop(what, None))
    elif what == "audio":
        index = request.form.get("index", type=int)
        if index is not None and 0 <= index < len(deck["slides"]):
            _delete_media(deck_id, deck["slides"][index].pop("audio", None))
    _save(deck_id, deck)
    return _back(deck_id)


@app.post("/deck/<deck_id>/slide/<int:index>/delete")
def delete_slide(deck_id, index):
    deck = _load(deck_id)
    if 0 <= index < len(deck["slides"]) and len(deck["slides"]) > 1:
        removed = deck["slides"].pop(index)
        for key in ("file", "image", "audio"):
            _delete_media(deck_id, removed.get(key))
        _save(deck_id, deck)
    return _back(deck_id)


@app.post("/deck/<deck_id>/slide/<int:index>/move")
def move_slide(deck_id, index):
    deck = _load(deck_id)
    slides = deck["slides"]
    target = index + (-1 if request.form.get("dir") == "up" else 1)
    if 0 <= index < len(slides) and 0 <= target < len(slides):
        slides[index], slides[target] = slides[target], slides[index]
        _save(deck_id, deck)
    return _back(deck_id)


@app.post("/deck/<deck_id>/slide/<int:index>/edit")
def edit_slide(deck_id, index):
    """Save one edited text field. field is a path like "title", "bullets.2" or "cards.0.body"."""
    deck = _load(deck_id)
    data = request.get_json(silent=True) or {}
    field, value = str(data.get("field", "")), str(data.get("value", "")).strip()
    if not 0 <= index < len(deck["slides"]) or not field:
        abort(400)

    parts = field.split(".")
    node = deck["slides"][index]
    try:
        for part in parts[:-1]:
            node = node[int(part)] if isinstance(node, list) else node[part]
        if isinstance(node, list):
            last = int(parts[-1])
            current = node[last]
        else:
            last = parts[-1]
            if last not in node and last not in TEXT_FIELDS:
                abort(400)
            current = node.get(last, "")
    except (KeyError, IndexError, ValueError, TypeError):
        abort(400)
    if not isinstance(current, str) or last in LOCKED_FIELDS:
        abort(400)  # only text can be edited, never file names or layout keys

    if isinstance(node, list) and not value and parts[0] in ("bullets", "items"):
        node.pop(last)                       # clearing a bullet removes it
    else:
        node[last] = value
    if field == "title" and index == 0:
        deck["title"] = value                # keep footers in sync with the cover title
    _save(deck_id, deck)
    return jsonify(ok=True)


@app.get("/media/<deck_id>/<name>")
def media(deck_id, name):
    path = MEDIA / secure_filename(deck_id) / secure_filename(name)
    if not path.is_file():
        abort(404)
    return send_file(path, conditional=True)  # conditional = supports seeking


@app.get("/download/<deck_id>")
def download(deck_id):
    deck = _load(deck_id)
    theme = _theme_arg()
    out = OUTPUTS / f"{deck_id}-{theme}.pptx"
    render_pptx(deck, out, theme, media_dir=MEDIA / deck_id)
    filename = secure_filename(deck["title"])[:60] or "presentation"
    return send_file(out, as_attachment=True, download_name=f"{filename}.pptx")


if __name__ == "__main__":
    app.run(debug=True)
