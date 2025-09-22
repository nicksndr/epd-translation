#!/usr/bin/env python3
import os
import sys
import argparse
from pathlib import Path
from typing import List, Optional

import deepl
from PyPDF2 import PdfReader
from fpdf import FPDF


# ---------- Helpers ----------
def read_pdf_text(pdf_path: Path) -> str:
    """Very simple text extraction without preserving layout."""
    reader = PdfReader(str(pdf_path))
    texts = []
    for page in reader.pages:
        try:
            texts.append(page.extract_text() or "")
        except Exception:
            texts.append("")
    return "\n\n".join(texts).strip()


def chunk_text(text: str, max_len: int = 4500) -> List[str]:
    """Split text into manageable chunks for the API."""
    if len(text) <= max_len:
        return [text]
    chunks, current = [], []
    length = 0
    parts = text.replace("\r", "").split("\n\n")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if length + len(part) + 2 > max_len:
            chunks.append("\n\n".join(current))
            current = [part]
            length = len(part)
        else:
            current.append(part)
            length += len(part) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def write_pdf(text: str, out_path: Path) -> None:
    """Create a simple PDF with the translated text (no original layout)."""
    pdf = FPDF()  # A4 default
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for paragraph in text.split("\n\n"):
        if not paragraph.strip():
            continue
        pdf.multi_cell(w=0, h=6, txt=paragraph)
        pdf.ln(2)
    pdf.output(str(out_path))


def resolve_target_lang(tgt: str, en_variant: str) -> str:
    """DeepL uses EN-GB/EN-US for English; other languages are just their code."""
    t = tgt.upper()
    if t == "EN":
        return en_variant.upper()  # EN-GB or EN-US
    return t


def find_glossary(translator: deepl.Translator, name: str, src: str, tgt: str) -> Optional[str]:
    """Return glossary_id if a glossary with this name and language pair exists."""
    s = src.upper()
    t = tgt.upper()
    for g in translator.list_glossaries():
        if g.name == name and g.source_lang.upper() == s and g.target_lang.upper() == t:
            return g.glossary_id
    return None


def translate_text(
    text: str,
    api_key: str,
    src: str,
    tgt: str,
    glossary_id: Optional[str],
    en_variant: str,
) -> str:
    """Translate using DeepL, optionally applying a glossary."""
    translator = deepl.Translator(api_key)
    deepl_target = resolve_target_lang(tgt, en_variant)
    chunks = chunk_text(text, max_len=4500)
    out: List[str] = []
    for ch in chunks:
        res = translator.translate_text(
            ch,
            source_lang=src.upper(),
            target_lang=deepl_target,
            glossary=glossary_id,  # None is fine if not found
        )
        out.append(res.text if hasattr(res, "text") else res[0].text)
    return "\n\n".join(out)


# ---------- CLI ----------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Translate a PDF using DeepL, optionally with a glossary."
    )
    p.add_argument("folder", help="Folder containing the PDF.")
    p.add_argument("filename", nargs="?", help="PDF filename (optional).")
    p.add_argument("--src", default="DE", help="Source language code (e.g., DE, EN, FR). Default: DE")
    p.add_argument("--tgt", default="EN", help="Target language code (e.g., EN, DE, FR). Default: EN")
    p.add_argument(
        "--en-variant",
        default="EN-GB",
        choices=["EN-GB", "EN-US"],
        help="If target is English, choose regional variant. Default: EN-GB",
    )
    p.add_argument(
        "--glossary",
        default="auto",
        help=(
            'Glossary name to use. Default "auto" looks for epd-<SRC>-<TGT>. '
            'Use "none" to skip glossaries.'
        ),
    )
    return p.parse_args()


def main():
    if "DEEPL_API_KEY" not in os.environ:
        print("Error: Please set the DEEPL_API_KEY environment variable.")
        sys.exit(1)
    api_key = os.environ["DEEPL_API_KEY"]

    args = parse_args()
    folder = Path(args.folder).expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        print(f"Error: Folder not found: {folder}")
        sys.exit(1)

    if args.filename:
        pdf_in = folder / args.filename
    else:
        pdfs = sorted(folder.glob("*.pdf"))
        if not pdfs:
            print(f"Error: No PDF found in folder: {folder}")
            sys.exit(1)
        pdf_in = pdfs[0]

    if not pdf_in.exists():
        print(f"Error: File not found: {pdf_in}")
        sys.exit(1)

    src = args.src.upper()
    tgt = args.tgt.upper()
    en_variant = args.en_variant.upper()

    print(f"Reading PDF: {pdf_in.name}")
    original_text = read_pdf_text(pdf_in)
    if not original_text.strip():
        print("Warning: No text extracted (scanned PDFs or unusual layout?).")
        print("A blank/short target PDF will still be created.")
    else:
        print(f"Extracted characters: {len(original_text)}")

    # Resolve glossary
    glossary_id = None
    glossary_name = None
    if args.glossary.lower() != "none":
        glossary_name = (
            args.glossary if args.glossary.lower() != "auto" else f"epd-{src}-{tgt}"
        )
        try:
            gid = find_glossary(deepl.Translator(api_key), glossary_name, src, tgt)
            if gid:
                glossary_id = gid
                print(f"Using glossary: {glossary_name} (id={gid})")
            else:
                print(f"Note: glossary '{glossary_name}' not found for {src}->{tgt}; continuing without it.")
        except Exception as e:
            print(f"Warning: failed to look up glossary '{glossary_name}': {e}. Continuing without it.")

    # Translate
    print(f"Translating ({src} → {tgt}) with DeepL…")
    translated = translate_text(original_text, api_key, src=src, tgt=tgt, glossary_id=glossary_id, en_variant=en_variant) if original_text else ""

    # Output
    out_name = f"{pdf_in.stem}_translated_{tgt.lower()}.pdf"
    pdf_out = folder / out_name
    print(f"Writing translated PDF: {pdf_out.name}")
    write_pdf(translated if translated else "[No extractable content found.]", pdf_out)

    print("Done.")


if __name__ == "__main__":
    main()
