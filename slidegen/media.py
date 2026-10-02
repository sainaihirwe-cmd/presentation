"""Helpers for media slides: supported types, YouTube links, photos and icons."""

import re
import urllib.request

VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}

AUDIO_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
}

# .webp/.gif are converted to .png on upload because PowerPoint can't use them reliably
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def save_image(source, folder, stem):
    """Save a photo (an upload or raw bytes), converting it to JPEG/PNG and limiting its size."""
    import io
    from PIL import Image, ImageOps

    stream = io.BytesIO(source) if isinstance(source, bytes) else source.stream
    with Image.open(stream) as im:
        im = ImageOps.exif_transpose(im)  # respect phone camera rotation
        im.thumbnail((2400, 2400))
        if im.mode in ("RGBA", "LA", "P"):
            name = stem + ".png"
            im.convert("RGBA").save(folder / name, optimize=True)
        else:
            name = stem + ".jpg"
            im.convert("RGB").save(folder / name, quality=88)
    return name


def make_audio_icon(theme, path, size=256):
    """Round speaker icon used as the clickable audio shape in PowerPoint."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([4, 4, size - 4, size - 4], fill=_rgb(theme["accent"]))
    s = size / 256
    white = (255, 255, 255, 255)
    d.rectangle([70 * s, 104 * s, 100 * s, 152 * s], fill=white)
    d.polygon([(100 * s, 104 * s), (138 * s, 72 * s), (138 * s, 184 * s), (100 * s, 152 * s)], fill=white)
    for r in (34, 58):
        d.arc([150 * s - r * s, 128 * s - r * s, 150 * s + r * s, 128 * s + r * s],
              -45, 45, fill=white, width=int(10 * s))
    img.save(path)
    return path


def youtube_id(url):
    """Return the video id for youtube.com/watch, youtu.be, /shorts and /embed links."""
    m = re.search(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/)|youtu\.be/)"
                  r"([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def fetch_youtube_thumbnail(video_id, path):
    """Download the YouTube thumbnail; returns False when offline or not found."""
    for size in ("maxresdefault", "hqdefault"):
        try:
            with urllib.request.urlopen(f"https://img.youtube.com/vi/{video_id}/{size}.jpg",
                                        timeout=5) as resp:
                data = resp.read()
            if len(data) > 2000:  # YouTube returns a tiny grey image for missing sizes
                path.write_bytes(data)
                return True
        except OSError:
            continue
    return False


def make_poster(theme, path, size=(1280, 720)):
    """Draw a themed cover image with a play button (used as the video's first frame)."""
    from PIL import Image, ImageDraw

    w, h = size
    c1, c2 = _rgb(theme["hero1"]), _rgb(theme["hero2"])
    # diagonal gradient drawn small, then scaled up smoothly
    small = Image.new("RGB", (32, 18))
    for y in range(18):
        for x in range(32):
            t = (x / 31 + y / 17) / 2
            small.putpixel((x, y), tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)))
    img = small.resize(size, Image.BILINEAR)

    draw = ImageDraw.Draw(img, "RGBA")
    r = h * 0.13
    cx, cy = w / 2, h / 2
    draw.ellipse([cx - r * 1.6, cy - r * 1.6, cx + r * 1.6, cy + r * 1.6], fill=(255, 255, 255, 40))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 235))
    tri = [(cx - r * 0.32, cy - r * 0.48), (cx - r * 0.32, cy + r * 0.48), (cx + r * 0.52, cy)]
    draw.polygon(tri, fill=_rgb(theme["hero1"]))
    img.save(path)
    return path


def _rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
