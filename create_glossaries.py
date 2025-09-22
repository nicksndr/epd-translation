#!/usr/bin/env python3
"""
Create/refresh DeepL glossaries from a Google Sheet (fetched as CSV at runtime).

Sheet assumptions:
- Columns are hard-coded by language names, e.g.: English, German, French
- Optional column "Active" (TRUE/Yes/1) to include only selected rows
- One row per term/phrase per language

What it does:
- Builds pairwise glossaries for every direction among languages present (e.g., EN<->DE, EN<->FR)
- Updates entries each run so your sheet remains the source of truth
"""

import os
import csv
import io
import re
import sys
import requests
from typing import Dict, List, Tuple, Optional
from pathlib import Path

import deepl


# ---- CONFIG: language columns and their DeepL codes --------------------------
# Extend this mapping as you add languages to the sheet.
LANG_COLUMNS = {
    "EN": "English",
    "DE": "German",
    "FR": "French",
    # "IT": "Italian",
    # "ES": "Spanish",
    # "NL": "Dutch",
    # ...
}

# Optional column to gate rows. If present, only rows with truthy values are used.
ACTIVE_COL = "Active"

# Glossary name template. Keep it stable so we update the same glossaries each run.
# Example: "epd-EN-DE"
GLOSSARY_NAME = "epd-{src}-{tgt}"

# If translating *to English* in your app later, choose EN-GB or EN-US there.
# (Glossary target lang here is just "EN/DE/FR/...", not the regional variant.)
# ------------------------------------------------------------------------------


def google_edit_url_to_csv_url(edit_url: str) -> str:
    """
    Convert an 'edit' URL to the public CSV export URL.
    Input:
      https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit?gid=<GID>#gid=<GID>
    Output:
      https://docs.google.com/spreadsheets/d/<SHEET_ID>/export?format=csv&gid=<GID>
    """
    m = re.search(r"/d/([a-zA-Z0-9-_]+)/", edit_url)
    if not m:
        raise ValueError("Could not extract SHEET_ID from URL.")
    sheet_id = m.group(1)

    # Prefer gid= in query or hash; default to 0 if not found
    gid_match = re.search(r"[?#&]gid=(\d+)", edit_url)
    gid = gid_match.group(1) if gid_match else "0"

    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def fetch_csv_text(csv_url: str) -> str:
    """
    Fetch CSV text from a (public) Google Sheets export URL.
    If your sheet is private, either publish-to-web (CSV) or use Google API auth instead.
    """
    resp = requests.get(csv_url, timeout=30)
    resp.raise_for_status()
    return resp.text


def read_rows_from_csv_text(csv_text: str) -> List[Dict[str, str]]:
    """Parse CSV into a list of dict rows (header-driven)."""
    buf = io.StringIO(csv_text)
    reader = csv.DictReader(buf)
    return [row for row in reader]


def truthy(val: Optional[str]) -> bool:
    if val is None:
        return True  # If no Active column, treat as active
    s = str(val).strip().lower()
    return s in ("true", "1", "yes", "y", "x")  # allow 'x' as in your screenshot


def build_pairs(rows: List[Dict[str, str]], src_col: str, tgt_col: str) -> Dict[str, str]:
    """Build a mapping src->tgt using the given column names, honoring ACTIVE_COL if present."""
    pairs: Dict[str, str] = {}
    for r in rows:
        if ACTIVE_COL in r and not truthy(r.get(ACTIVE_COL)):
            continue
        src = (r.get(src_col) or "").strip()
        tgt = (r.get(tgt_col) or "").strip()
        if src and tgt:
            pairs[src] = tgt
    return pairs


def ensure_glossary(translator, name: str, source_lang: str, target_lang: str, pairs: dict):
    # try to find existing glossary
    for g in translator.list_glossaries():
        if g.name == name and g.source_lang.upper() == source_lang.upper() and g.target_lang.upper() == target_lang.upper():
            # update if API supports it; otherwise delete+recreate
            try:
                translator.set_glossary_entries(g, entries=pairs)  # new SDKs
                return g
            except AttributeError:
                translator.delete_glossary(g.glossary_id)          # old SDKs
                break

    # create new (or after delete)
    return translator.create_glossary(
        name=name,
        source_lang=source_lang,
        target_lang=target_lang,
        entries=pairs,  # dict of source->target
    )


def save_pairs_to_csv(pairs: dict, src: str, tgt: str, folder: Path = Path("glossaries")):
    """Save a glossary mapping src->tgt as CSV into glossaries/ folder."""
    folder.mkdir(parents=True, exist_ok=True)
    out_path = folder / f"{src}_{tgt}.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([src, tgt])  # header
        for s, t in pairs.items():
            writer.writerow([s, t])
    print(f"Saved local glossary CSV: {out_path}")

def main():
    if "DEEPL_API_KEY" not in os.environ:
        print("Error: set DEEPL_API_KEY in your environment.")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage: python create_glossaries_from_sheet.py <google_sheet_edit_url>")
        sys.exit(1)

    edit_url = sys.argv[1]
    csv_url = google_edit_url_to_csv_url(edit_url)
    print(f"Fetching CSV from: {csv_url}")

    try:
        csv_text = fetch_csv_text(csv_url)
    except Exception as e:
        print(f"Error fetching CSV: {e}")
        sys.exit(1)

    rows = read_rows_from_csv_text(csv_text)
    if not rows:
        print("No rows found in CSV (empty or headers missing).")
        sys.exit(1)

    # Validate required columns exist
    headers = set(rows[0].keys())
    missing = [col for col in LANG_COLUMNS.values() if col not in headers]
    if len(missing) == len(LANG_COLUMNS):
        print(f"Error: none of the language columns were found. Expected any of: {list(LANG_COLUMNS.values())}")
        sys.exit(1)
    elif missing:
        print(f"Warning: missing columns (will skip directions involving them): {missing}")

    translator = deepl.Translator(os.environ["DEEPL_API_KEY"])

        # --- Only allow EN->DE glossary for now ---
    src, tgt = "EN", "DE"
    src_col, tgt_col = LANG_COLUMNS[src], LANG_COLUMNS[tgt]
    pairs = build_pairs(rows, src_col=src_col, tgt_col=tgt_col)
    if pairs:
        save_pairs_to_csv(pairs, src, tgt)  # keep local CSV copy
        name = GLOSSARY_NAME.format(src=src, tgt=tgt)
        print(f"Syncing glossary '{name}' ({src}->{tgt}) with {len(pairs)} entries...")
        g = ensure_glossary(translator, name=name,
                            source_lang=src, target_lang=tgt, pairs=pairs)
        print(f"Glossary ready: {name} (id={g.glossary_id})")
    else:
        print("No EN->DE pairs found, nothing to sync.")

    # --- Future when on paid plan ---
    # Build pairwise glossaries for every direction among present languages
    # langs_present = [code for code, col in LANG_COLUMNS.items() if col in headers]
    # created_or_updated: List[Tuple[str, str, str]] = []

    # for src in langs_present:
    #     for tgt in langs_present:
    #         if src == tgt:
    #             continue
    #         src_col = LANG_COLUMNS[src]
    #         tgt_col = LANG_COLUMNS[tgt]
    #         pairs = build_pairs(rows, src_col=src_col, tgt_col=tgt_col)
    #         if not pairs:
    #             continue

    #         # Save as CSV locally
    #         save_pairs_to_csv(pairs, src, tgt)

    #         # Push/update to DeepL
    #         name = GLOSSARY_NAME.format(src=src, tgt=tgt)
    #         print(f"Syncing glossary '{name}' ({src}->{tgt}) with {len(pairs)} entries...")
    #         g = ensure_glossary(translator, name=name, source_lang=src, target_lang=tgt, pairs=pairs)
    #         created_or_updated.append((name, g.glossary_id, f"{src}->{tgt}"))

    # if not created_or_updated:
    #     print("No glossaries created/updated (no valid pairs found).")
    #     sys.exit(0)

    # print("\nDone. Glossaries ready:")
    # for name, gid, pair in created_or_updated:
    #     print(f" - {name} [{pair}]  id={gid}")


if __name__ == "__main__":
    main()
