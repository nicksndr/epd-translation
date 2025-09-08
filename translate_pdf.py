#!/usr/bin/env python3
import os
import sys
from pathlib import Path
from typing import List

import deepl
from PyPDF2 import PdfReader
from fpdf import FPDF


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
    # Roughly split by paragraphs/sentences
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


def translate_text_de_to_en(text: str, api_key: str) -> str:
    """Translate German → English via DeepL."""
    translator = deepl.Translator(api_key)
    chunks = chunk_text(text, max_len=4500)
    translated_chunks = []
    for ch in chunks:
        # source_lang="DE" is optional but helps enforce direction
        res = translator.translate_text(ch, source_lang="DE", target_lang="EN-GB")
        translated_chunks.append(res.text if hasattr(res, "text") else res[0].text)
    return "\n\n".join(translated_chunks)


def write_pdf(text: str, out_path: Path) -> None:
    """Create a simple PDF with the translated text (no original layout)."""
    pdf = FPDF()  # A4 default
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)

    # FPDF has a simple text engine; use multi_cell for line wrapping
    # Split into smaller paragraphs for cleaner page breaks
    for paragraph in text.split("\n\n"):
        if not paragraph.strip():
            continue
        pdf.multi_cell(w=0, h=6, txt=paragraph)
        pdf.ln(2)

    pdf.output(str(out_path))


def main():
    if "DEEPL_API_KEY" not in os.environ:
        print("Error: Please set the DEEPL_API_KEY environment variable.")
        sys.exit(1)

    api_key = os.environ["DEEPL_API_KEY"]

    # Usage:
    #   python translate_pdf.py /path/to/folder file.pdf
    # or:
    #   python translate_pdf.py /path/to/folder
    #   (will take the first .pdf in the folder)
    if len(sys.argv) < 2:
        print("Usage: python translate_pdf.py <folder_path> [filename.pdf]")
        sys.exit(1)

    folder = Path(sys.argv[1]).expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        print(f"Error: Folder not found: {folder}")
        sys.exit(1)

    if len(sys.argv) >= 3:
        pdf_in = folder / sys.argv[2]
    else:
        pdfs = sorted(folder.glob("*.pdf"))
        if not pdfs:
            print(f"Error: No PDF found in folder: {folder}")
            sys.exit(1)
        pdf_in = pdfs[0]

    if not pdf_in.exists():
        print(f"Error: File not found: {pdf_in}")
        sys.exit(1)

    print(f"Reading PDF: {pdf_in.name}")
    original_text = read_pdf_text(pdf_in)
    if not original_text.strip():
        print("Warning: No text extracted (scanned PDFs or unusual layout?).")
        print("A blank/short target PDF will still be created.")
    else:
        print(f"Extracted characters: {len(original_text)}")

    print("Translating (DE → EN) with DeepL…")
    translated = translate_text_de_to_en(original_text, api_key) if original_text else ""

    out_name = pdf_in.stem + "_translated_en.pdf"
    pdf_out = folder / out_name
    print(f"Writing translated PDF: {pdf_out.name}")
    write_pdf(translated if translated else "[No extractable content found.]", pdf_out)

    print("Done.")


if __name__ == "__main__":
    main()
