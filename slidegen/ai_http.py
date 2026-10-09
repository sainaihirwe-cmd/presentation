"""Claude over plain HTTPS, for the Android app.

The phone build can't install the anthropic SDK (its pydantic dependency has no Android
build), so this sends the same request as ai.generate_deck with only the standard library,
streaming the answer so the progress page can show slides as they are written.
"""

import json
import urllib.error
import urllib.request
from types import SimpleNamespace

from .ai import MODEL, SYSTEM, AIError, _progress_from_partial

API_URL = "https://api.anthropic.com/v1/messages"


def _obj(required, **props):
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}
# same shape as ai.AIDeck
SCHEMA = _obj(
    ["title", "subtitle", "agenda_title", "closing_title", "closing_subtitle", "slides"],
    title=_STR, subtitle=_STR, agenda_title=_STR, closing_title=_STR, closing_subtitle=_STR,
    slides={"type": "array", "items": _obj(
        ["layout", "title", "subtitle", "bullets", "cards", "stats", "table", "text", "notes"],
        layout={"type": "string",
                "enum": ["section", "bullets", "cards", "stats", "table", "statement"]},
        title=_STR, subtitle=_STR, bullets=_STRS,
        cards={"type": "array", "items": _obj(["head", "body"], head=_STR, body=_STR)},
        stats={"type": "array", "items": _obj(["value", "label"], value=_STR, label=_STR)},
        table={"type": "array", "items": _STRS},
        text=_STR, notes=_STR)},
)


class _Item(SimpleNamespace):
    """Stands in for the Pydantic models that ai.to_deck reads."""

    def model_dump(self):
        return dict(vars(self))


def _wrap(value):
    if isinstance(value, dict):
        return _Item(**{k: _wrap(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


def generate_over_http(api_key, request, progress=None):
    body = {
        "model": MODEL,
        "max_tokens": 16000,
        "stream": True,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": request}],
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        # If a safety classifier declines, the API retries on a fallback model.
        "fallbacks": "default",
    }
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode("utf-8"), headers={
        "content-type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "server-side-fallback-2026-07-01",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=600)
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read())["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = exc.reason
        if exc.code == 401:
            raise AIError("The Anthropic API key was rejected. Check it in the 🔑 API key box.")
        if exc.code == 403:
            raise AIError("This API key is not allowed to use the model. Check your Anthropic account.")
        if exc.code == 429:
            raise AIError("Too many requests right now. Wait a minute and try again.")
        raise AIError(f"The AI service returned an error ({exc.code}): {message}")
    except (urllib.error.URLError, OSError):
        raise AIError("Could not reach the Anthropic API. Check your internet connection.")

    text, stop_reason = "", None
    with resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:])
            kind = event.get("type")
            if kind == "content_block_start" and event["content_block"].get("type") == "fallback":
                text = ""  # a fallback model starts the answer again
            elif kind == "content_block_delta" and event["delta"].get("type") == "text_delta":
                text += event["delta"]["text"]
                if progress:
                    progress(_progress_from_partial(text))
            elif kind == "message_delta":
                stop_reason = event["delta"].get("stop_reason") or stop_reason
            elif kind == "error":
                raise AIError("The AI service returned an error: " + event["error"].get("message", ""))

    if stop_reason == "refusal":
        raise AIError("The AI declined to write this presentation. Try rephrasing the prompt.")
    if stop_reason == "max_tokens":
        raise AIError("The presentation was too long to finish. Ask for fewer slides.")
    try:
        return _wrap(json.loads(text))
    except ValueError:
        raise AIError("Claude's answer was incomplete. Please try again.")
