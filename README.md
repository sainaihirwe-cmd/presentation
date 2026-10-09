# SlideGen — documents and ideas to beautiful presentations

Upload a document (.docx, .pdf, .md, .txt) **or describe the presentation you want** and let
AI write it. Add photos, videos and audio, edit any text, then present in the browser or
download as PowerPoint (.pptx).

## Setup

```
pip install -r requirements.txt
python make_sample.py          # optional: creates samples/project_report.docx
python app.py                  # then open http://127.0.0.1:5000
```

## Use it on your phone (as an app)

SlideGen is an installable web app: put it online once, then open it on any phone with a normal
web address. No IP address, and the phone does not need to be on your computer's Wi-Fi.

1. **Put it online (free) with Render.** Push this folder to GitHub, sign in at
   https://render.com with GitHub, choose **New → Blueprint**, and pick this repository.
   Render reads `render.yaml` and gives you an address like `https://slidegen.onrender.com`.
   In the Render dashboard, set `SITE_PASSWORD` (and optionally `ANTHROPIC_API_KEY`).
2. **Install it on the phone.** Open that address on the phone, then:
   - **Android (Chrome):** menu **⋮** → **Add to Home screen / Install app**.
   - **iPhone (Safari):** **Share** → **Add to Home Screen**.

SlideGen then has its own icon and opens full screen like a normal app. It needs mobile data
or Wi-Fi, because the slides are made on the server. On Render's free plan the app sleeps after
about 15 minutes without visitors. The next visit takes up to a minute to start, and decks
saved on the server are cleared when it restarts, so download the .pptx files you want to keep.

## Create slides

- **📄 From a document**: drop in a file, pick a design, click **Generate slides**.
- **✨ From a prompt**, two ways:
  - **🌍 Free (Wikipedia)**, no account or key: type a topic ("Solar energy", "History of
    Rwanda"), choose the number of slides and the language (English, Français, Kinyarwanda,
    Kiswahili). SlideGen reads the Wikipedia article, makes the slides in a few seconds, and uses
    the article's main photo as the cover. The source is credited on the last slide
    (Wikipedia text is CC BY-SA). Needs internet.
  - **✨ Claude AI** writes an original deck from any idea (pitches, lessons, reports), with
    audience, tone and speaker notes. It writes in the language you use in the prompt.
  - **No key needed if Claude Code is installed and signed in** on this computer (as it is when
    you use Claude in VS Code). SlideGen runs the `claude` command in the background with your
    Claude login, and the work counts toward your Claude plan's usage. The page shows
    "uses your Claude login" when this is available.
  - Otherwise, Claude needs an Anthropic API key, set once: get one at https://console.anthropic.com/settings/keys
    and paste it in the **🔑 API key** box. It is saved in `.env` in this folder (git ignores it).
    The `ANTHROPIC_API_KEY` environment variable also works.
  - Each deck is one request to Claude Opus 5.5, roughly US$0.10–0.30 for a 10-slide deck
    (more slides cost more).

## On the deck page

| Feature | How |
|---|---|
| Switch design | Click the coloured dots (11 designs) |
| Edit text | **✎ Edit text**, click any text on a slide, type, then click outside. Clear a bullet to remove it |
| Reorder / delete | Hover a slide: **↑ ↓** move it, **✕** deletes it |
| Add video | **＋ Add media** → 🎬 Video: upload .mp4/.mov/.m4v/.webm, or paste a YouTube link |
| Add photo | **＋ Add media** → 🖼 Photo. Designs: *Full screen*, *Photo left*, *Photo right*, *Framed*, or *Cover background* for the title slide |
| Add audio | **＋ Add media** → 🎵 Audio (.mp3/.m4a/.wav/.aac): *background music* for the whole talk, or narration *on one slide* |
| Present | **▶ Present**. ← → navigate, **N** shows speaker notes, **M** turns music on/off, **Esc** exits |
| Download | **⬇ Download .pptx** |

### What ends up in the PowerPoint

- Photos, uploaded videos and audio are **embedded** in the .pptx.
- YouTube links become the video's thumbnail and open the video when clicked (needs internet).
- Background music is placed on the first slide as a speaker icon. In PowerPoint it plays
  when you click it. In the browser presenter it plays through the whole presentation.
- Slide audio is a speaker icon on that slide. Speaker notes go into PowerPoint's notes.

## Use the command line

```
python convert.py samples/project_report.docx --theme aurora
python convert.py notes.md --theme forest -o my_talk.pptx
```

Themes: `midnight`, `aurora`, `sunset`, `forest`, `corporate`, `ocean`, `rose`, `mono`,
`neon`, `sand`, `royal`.

## How it works

1. **parser.py** reads a file into blocks (headings, paragraphs, bullets, tables).
2. **builder.py** decides the slides for a document: title, agenda, section dividers, and a
   layout for the content:
   - `Term: description` lists → **cards**
   - lists that start with numbers (`32% increase…`) → **stats**
   - tables → **table** slides
   - a single short paragraph → **statement** (quote) slide
   - everything else → **bullets** (max 5 per slide, long sentences shortened)
3. **ai.py** asks Claude for a structured outline that uses the same layouts (prompt mode).
4. **renderer.py** draws the .pptx with python-pptx. `templates/slide.html` +
   `static/slides.css` draw the same layouts for the browser preview.
5. **media.py** handles videos, photos (resized, rotated from phone EXIF) and audio.

Decks are saved as JSON in `outputs/`, uploaded media in `outputs/media/<deck id>/`.

## Tips for best results

- Use real headings (Word "Heading 1/2" styles, or `#`/`##` in Markdown).
  Heading 1 = sections, Heading 2 = individual slides.
- Write lists as bullets; use `Name: description` to get card layouts.
- For AI decks, say who the audience is and what to cover. Check any numbers before presenting.
- Scanned PDFs (images only) have no text to extract.

## Add a theme

Add an entry to `THEMES` in `slidegen/themes.py`. Both the preview and the PowerPoint use it.

## Deploy on Render

The repo includes `render.yaml`, so Render can set everything up:

1. On https://dashboard.render.com choose **New → Blueprint** and pick this GitHub repo
   (or **New → Web Service**, which reads the same settings).
2. Optional environment variables (Render dashboard → your service → **Environment**):
   - `ANTHROPIC_API_KEY`: turns on ✨ Claude mode. On a server there's no Claude Code login,
     so Claude needs a key there. 🌍 Free mode and document upload work without it.
   - `SITE_PASSWORD`: visitors must enter it (any username). **Set this if you add an API key**,
     otherwise anyone who finds the site can spend your credit.
3. Render runs `gunicorn app:app` and gives you a URL like `https://slidegen.onrender.com`.

Notes for the free plan: the site sleeps after 15 minutes without visitors (the first visit
then takes about a minute), and the disk is temporary, so uploaded files and decks are deleted
when the service restarts or redeploys. Download your .pptx files to keep them.
