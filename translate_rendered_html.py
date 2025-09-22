#!/usr/bin/env python3
# Translate already-rendered HTML (no Jinja left) using DeepL.
from __future__ import annotations
import os
import argparse
from typing import Optional
import deepl

IGNORE_TAGS = ["script", "style", "code", "pre"]  # extend if needed

def _resolve_target(tgt: str, en_variant: str) -> str:
    return en_variant if tgt.upper() == "EN" else tgt.upper()

def find_glossary_id(translator: deepl.Translator, name: str, src: str, tgt: str) -> Optional[str]:
    s, t = src.upper(), tgt.upper()
    for g in translator.list_glossaries():
        if g.name == name and g.source_lang.upper() == s and g.target_lang.upper() == t:
            return g.glossary_id
    return None

def translate_rendered_html(html: str, *, api_key: str, src: str, tgt: str,
                            glossary_id: Optional[str] = None, en_variant: str = "EN-GB") -> str:
    tr = deepl.Translator(api_key)
    result = tr.translate_text(
        html,
        source_lang=src.upper(),
        target_lang=_resolve_target(tgt, en_variant),
        tag_handling="html",
        ignore_tags=IGNORE_TAGS,     # HTML structure preserved; code/css ignored
        glossary=glossary_id,        # OK if None
    )
    return result.text if hasattr(result, "text") else result[0].text

if __name__ == "__main__":
    p = argparse.ArgumentParser("Translate a rendered HTML file with DeepL.")
    p.add_argument("input_html")
    p.add_argument("output_html")
    p.add_argument("--src", default="EN")
    p.add_argument("--tgt", default="DE")
    p.add_argument("--glossary", default="auto", help='Use "auto" for epd-<SRC>-<TGT>, or "none".')
    p.add_argument("--en-variant", default="EN-GB", choices=["EN-GB", "EN-US"])
    args = p.parse_args()

    api_key = os.environ.get("DEEPL_API_KEY")
    if not api_key:
        raise SystemExit("Set DEEPL_API_KEY.")

    glossary_id = None
    if args.glossary.lower() != "none":
        name = f"epd-{args.src.upper()}-{args.tgt.upper()}" if args.glossary.lower() == "auto" else args.glossary
        try:
            glossary_id = find_glossary_id(deepl.Translator(api_key), name, args.src, args.tgt)
            if glossary_id:
                print(f"Using glossary {name} ({args.src}->{args.tgt})")
            else:
                print(f"Note: glossary '{name}' not found; continuing without.")
        except Exception as e:
            print(f"Glossary lookup failed: {e} (continuing without).")

    html = open(args.input_html, "r", encoding="utf-8").read()
    out = translate_rendered_html(html, api_key=api_key, src=args.src, tgt=args.tgt,
                                  glossary_id=glossary_id, en_variant=args.en_variant)
    open(args.output_html, "w", encoding="utf-8").write(out)
    print(f"Translated → {args.output_html}")