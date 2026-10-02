"""Command-line version: python convert.py my_document.docx [--theme aurora] [-o out.pptx]"""

import argparse
from pathlib import Path

from slidegen import THEMES, build_deck, parse_document, render_pptx


def main():
    ap = argparse.ArgumentParser(description="Convert a document into a designed PowerPoint deck.")
    ap.add_argument("document")
    ap.add_argument("--theme", default="midnight", choices=sorted(THEMES))
    ap.add_argument("-o", "--output")
    args = ap.parse_args()

    src = Path(args.document)
    out = Path(args.output) if args.output else src.with_suffix(f".{args.theme}.pptx")
    deck = build_deck(parse_document(src), fallback_title=src.stem.replace("_", " ").title())
    render_pptx(deck, out, args.theme)
    print(f"{len(deck['slides'])} slides -> {out}")
    for i, s in enumerate(deck["slides"], 1):
        print(f"  {i:2d}. [{s['layout']}] {s['title']}")


if __name__ == "__main__":
    main()
