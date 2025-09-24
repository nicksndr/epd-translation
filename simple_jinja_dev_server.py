#!/usr/bin/env python3
"""
Render Jinja -> HTML, optionally translate HTML with DeepL, then HTML -> PDF via weasyprint.
"""

from __future__ import annotations
import os, json, argparse, subprocess
from pathlib import Path
from typing import Optional
from jinja2 import Environment, FileSystemLoader, select_autoescape, StrictUndefined
import deepl
from weasyprint import HTML

IGNORE_TAGS = ["script", "style", "code", "pre"]  # do not translate these blocks

# ---------- Rendering ----------
def build_env(templates_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,  # fail if a variable is missing
    )

def render_template(env: Environment, template_name: str, context: dict) -> str:
    tpl = env.get_template(template_name)
    return tpl.render(**context)

# ---------- DeepL translation ----------
def _resolve_target(tgt: str, en_variant: str) -> str:
    return en_variant if tgt.upper() == "EN" else tgt.upper()

def find_glossary_id(translator: deepl.Translator, name: str, src: str, tgt: str) -> Optional[str]:
    s, t = src.upper(), tgt.upper()
    for g in translator.list_glossaries():
        if g.name == name and g.source_lang.upper() == s and g.target_lang.upper() == t:
            return g.glossary_id
    return None

def translate_html(html: str, *, api_key: str, src: str, tgt: str, glossary_id: Optional[str], en_variant: str) -> str:
    tr = deepl.Translator(api_key)
    res = tr.translate_text(
        html,
        source_lang=src.upper(),
        target_lang=_resolve_target(tgt, en_variant),
        tag_handling="html",
        ignore_tags=IGNORE_TAGS,
        glossary=glossary_id,  # ok if None
    )
    return res.text if hasattr(res, "text") else res[0].text

# ---------- PDF ----------
def html_to_pdf(input_html_path, output_pdf_path):
    HTML(filename=str(input_html_path)).write_pdf(str(output_pdf_path))

# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(description="Render Jinja -> HTML -> (translate) -> PDF")
    ap.add_argument("--template", default="example.j2", help="Template file (default: example.j2)")
    ap.add_argument("--templates-dir", default=".", help="Templates directory (default: current)")
    ap.add_argument("--data", help="Optional JSON file with context data")
    ap.add_argument("--outdir", default="out", help="Output directory (default: out)")
    ap.add_argument("--translate", action="store_true", help="Translate the rendered HTML with DeepL")
    ap.add_argument("--src", default="EN", help="Source language (default: EN)")
    ap.add_argument("--tgt", default="DE", help="Target language (default: DE)")
    ap.add_argument("--en-variant", default="EN-GB", choices=["EN-GB", "EN-US"], help="English variant if tgt=EN")
    ap.add_argument("--glossary", default="none", help='Glossary name (e.g. "epd-EN-DE"), or "auto", or "none"')
    args = ap.parse_args()

    templates_dir = Path(args.templates_dir).resolve()
    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    # Sample context if no JSON provided
    context = {
        "company": {"name": "Emidat", "country": "Norway"},
        "product": {
            "name": "Low-Carbon AAC powder",
            "slug": "low-carbon-aac-block",
            "is_premium": True,
            "features": [
                "High thermal efficiency",
                "Lightweight and easy to install",
                "Lower embodied carbon than conventional alternatives",
            ],
            "specs": {
                "Density": "525 kg/m³", 
                "Compressive strength": "4.0 MPa",
                "Declared unit": "1 m² wall, 100 mm thickness",
            },
        },
    }
    if args.data:
        context = json.loads(Path(args.data).read_text(encoding="utf-8"))

    # 1) Render Jinja -> HTML (source language HTML)
    env = build_env(templates_dir)
    rendered_html = render_template(env, args.template, context)
    src_html_path = outdir / "rendered_src.html"
    src_html_path.write_text(rendered_html, encoding="utf-8")
    print(f"Rendered HTML → {src_html_path}")

    html_for_pdf_path = src_html_path

    # 2) (Optional) Translate HTML
    if args.translate:
        api_key = os.environ.get("DEEPL_API_KEY")
        if not api_key:
            raise SystemExit("Set DEEPL_API_KEY to translate.")

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

        translated_html = translate_html(
            rendered_html,
            api_key=api_key,
            src=args.src,
            tgt=args.tgt,
            glossary_id=glossary_id,
            en_variant=args.en_variant,
        )
        tgt_html_path = outdir / f"rendered_{args.tgt.lower()}.html"
        tgt_html_path.write_text(translated_html, encoding="utf-8")
        print(f"Translated HTML → {tgt_html_path}")
        html_for_pdf_path = tgt_html_path

    # 3) HTML -> PDF
    pdf_path = outdir / f"rendered_{('src' if html_for_pdf_path==src_html_path else args.tgt.lower())}.pdf"
    try:
        html_to_pdf(html_for_pdf_path, pdf_path)
        print(f"PDF → {pdf_path}")
    except FileNotFoundError:
        print("weasyprint not found. Install it with Homebrew: brew install weasyprint")
        print(f"You can still open the HTML in a browser: {html_for_pdf_path}")

if __name__ == "__main__":
    main()