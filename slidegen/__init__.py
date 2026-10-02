"""SlideGen - turn documents into beautiful presentations."""

from .parser import parse_document
from .builder import build_deck
from .renderer import render_pptx
from .themes import THEMES

__all__ = ["parse_document", "build_deck", "render_pptx", "THEMES"]
