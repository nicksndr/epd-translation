#!/usr/bin/env python3
# translate_jinja_html.py
# Translate a Jinja2+HTML template with DeepL while preserving Jinja & markup.

from __future__ import annotations

import os
import re
import ast
import json
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import deepl


# --------- 1) Jinja masking (preserve all Jinja exactly) ---------
JINJA_PATTERNS = [
    (r"{%\s*raw\s*%}.*?{%\s*endraw\s*%}", "RAW"),  # {% raw %}...{% endraw %}
    (r"{#.*?#}", "CMT"),                            # {# comment #}
    (r"{%.*?%}", "STMT"),                           # {% statement %}
    (r"{{.*?}}", "EXPR"),                           # {{ expression }}
]

def mask_jinja(src: str) -> tuple[str, Dict[str, str]]:
    mapping: Dict[str, str] = {}
    masked = src
    counter = 0
    # run in order; each pass replaces current matches with inert HTML elements
    for pat, _ in JINJA_PATTERNS:
        while True:
            m = re.search(pat, masked, flags=re.DOTALL)
            if not m:
                break
            key = f"J{counter:06d}"
            placeholder = f'<x-jinja data-k="{key}"></x-jinja>'
            mapping[key] = masked[m.start(): m.end()]
            masked = masked[: m.start()] + placeholder + masked[m.end():]
            counter += 1
    return masked, mapping

def unmask_jinja(masked: str, mapping: Dict[str, str]) -> str:
    return re.sub(
        r'<x-jinja data-k="(J\d{6})"></x-jinja>',
        lambda m: mapping[m.group(1)],
        masked,
    )


# --------- 2) Handle heading2/heading3 macro calls -------------
# We want the *content* of {{ heading2("...") }} / {{ heading3("...") }} translated.
# For literal args we temporarily convert to <x-h2>...</x-h2> / <x-h3>...</x-h3>
# so DeepL translates the inner text during the HTML pass.
# For non-literal args (e.g., unit_type|first_upper ~ " unit"), we translate
# only the string literals inside the code (e.g., " unit") and keep the rest.

LITERAL_MACRO_RE = re.compile(
    r"{{\s*heading(?P<lvl>[23])\(\s*(?P<string>"
    r"(?:'[^'\\]*(?:\\.[^'\\]*)*')|(?:\"[^\"\\]*(?:\\.[^\"\\]*)*\"))\s*\)\s*}}",
    re.DOTALL,
)

ANY_MACRO_RE = re.compile(
    r"{{\s*heading(?P<lvl>[23])\(\s*(?P<arg>.*?)\s*\)\s*}}",
    re.DOTALL,
)

STRING_LIT_RE = re.compile(
    r"(?:'[^'\\]*(?:\\.[^'\\]*)*'|\"[^\"\\]*(?:\\.[^\"\\]*)*\")",
    re.DOTALL,
)

def replace_literal_heading_macros_with_tags(src: str) -> str:
    out: List[str] = []
    last = 0
    for m in LITERAL_MACRO_RE.finditer(src):
        out.append(src[last: m.start()])
        lvl = m.group("lvl")
        s_literal = m.group("string")
        try:
            content = ast.literal_eval(s_literal)
        except Exception:
            content = s_literal.strip("\"'")
        out.append(f"<x-h{lvl}>{content}</x-h{lvl}>")
        last = m.end()
    out.append(src[last:])
    return "".join(out)

def restore_heading_tags_to_macros(html: str) -> str:
    def repl_h2(m):
        q = json.dumps(m.group(1))
        return "{{ heading2(" + q + ") }}"
    def repl_h3(m):
        q = json.dumps(m.group(1))
        return "{{ heading3(" + q + ") }}"
    html = re.sub(r"<x-h2>(.*?)</x-h2>", repl_h2, html, flags=re.DOTALL)
    html = re.sub(r"<x-h3>(.*?)</x-h3>", repl_h3, html, flags=re.DOTALL)
    return html

def translate_string_literals_in_nonliteral_macros(
    src: str,
    translator: deepl.Translator,
    source_lang: str,
    target_lang: str,
    en_variant: str,
    glossary: Optional[str],
) -> str:
    # Gather literals inside heading2/3 where the arg is not a pure string
    to_translate: List[str] = []
    spans: List[Tuple[int, int, List[Tuple[int, int, str]]]] = []
    for m in ANY_MACRO_RE.finditer(src):
        arg = m.group("arg").strip()
        if (arg.startswith(("'", '"')) and arg.endswith(("'", '"'))):
            continue  # handled by <x-hX> tags
        # collect string literal spans in this arg
        arg_span_literals: List[Tuple[int, int, str]] = []
        for sm in STRING_LIT_RE.finditer(arg):
            lit = sm.group(0)
            try:
                content = ast.literal_eval(lit)
            except Exception:
                content = lit.strip("\"'")
            if content:
                to_translate.append(content)
                arg_span_literals.append((sm.start(), sm.end(), lit))
        if arg_span_literals:
            spans.append((m.start("arg"), m.end("arg"), arg_span_literals))

    if not to_translate:
        return src

    # Translate list in one call (DeepL accepts list)
    tgt_deepl = en_variant if target_lang.upper() == "EN" else target_lang.upper()
    results = translator.translate_text(
        to_translate, source_lang=source_lang.upper(), target_lang=tgt_deepl, glossary=glossary
    )
    translated = [r.text for r in (results if isinstance(results, list) else [results])]

    # Map back in order of discovery
    it = iter(translated)
    new_src = []
    last = 0
    for arg_start, arg_end, lits in spans:
        new_src.append(src[last:arg_start])
        arg_text = src[arg_start:arg_end]
        arg_out = []
        a_last = 0
        for s0, s1, lit_token in lits:
            arg_out.append(arg_text[a_last:s0])
            new_text = next(it)
            # keep original quote style
            q = lit_token[0]
            if q == "'":
                escaped = new_text.replace("\\", "\\\\").replace("'", "\\'")
                arg_out.append("'" + escaped + "'")
            else:
                arg_out.append(json.dumps(new_text))
            a_last = s1
        arg_out.append(arg_text[a_last:])
        new_src.append("".join(arg_out))
        last = arg_end
    new_src.append(src[last:])
    return "".join(new_src)


# --------- 3) HTML translation via DeepL (preserve tags) ---------
def translate_html_with_deepl(
    html_text: str,
    api_key: str,
    src: str,
    tgt: str,
    glossary: Optional[str] = None,
    en_variant: str = "EN-GB",
) -> str:
    translator = deepl.Translator(api_key)
    # translate string-literals inside non-literal heading macros first (so they survive masking)
    html_text = translate_string_literals_in_nonliteral_macros(
        html_text, translator, src, tgt, en_variant, glossary
    )
    # literal heading macros -> tags so DeepL translates their inner text
    html_text = replace_literal_heading_macros_with_tags(html_text)

    # mask the rest of Jinja
    masked, map_jinja = mask_jinja(html_text)

    # translate visible text while preserving HTML tags
    deepl_target = en_variant if tgt.upper() == "EN" else tgt.upper()
    result = translator.translate_text(
        masked,
        source_lang=src.upper(),
        target_lang=deepl_target,
        tag_handling="html",
        ignore_tags=["x-jinja", "script", "style", "pre", "code"],
        glossary=glossary,
    )
    translated_masked = result.text if hasattr(result, "text") else result[0].text

    # restore Jinja and convert <x-h2>/<x-h3> back to macros
    unmasked = unmask_jinja(translated_masked, map_jinja)
    final_text = restore_heading_tags_to_macros(unmasked)
    return final_text


# --------- 4) CLI ---------
def find_glossary_id(translator: deepl.Translator, name: str, src: str, tgt: str) -> Optional[str]:
    s, t = src.upper(), tgt.upper()
    for g in translator.list_glossaries():
        if g.name == name and g.source_lang.upper() == s and g.target_lang.upper() == t:
            return g.glossary_id
    return None

def main():
    ap = argparse.ArgumentParser("Translate a Jinja2 (.j2) template with DeepL, preserving Jinja & HTML.")
    ap.add_argument("input", help="Input .j2 file")
    ap.add_argument("output", help="Output .j2 file")
    ap.add_argument("--src", default="EN", help="Source lang (EN, DE, FR, ...). Default EN")
    ap.add_argument("--tgt", default="DE", help="Target lang (DE, EN, FR, ...). Default DE")
    ap.add_argument("--en-variant", default="EN-GB", choices=["EN-GB", "EN-US"],
                    help="If target is English, pick EN-GB or EN-US (default EN-GB)")
    ap.add_argument("--glossary", default="auto",
                    help='Glossary name; "auto" uses epd-<SRC>-<TGT>, "none" disables.')
    args = ap.parse_args()

    if "DEEPL_API_KEY" not in os.environ:
        raise SystemExit("Set DEEPL_API_KEY first.")

    inp = Path(args.input)
    outp = Path(args.output)
    raw = inp.read_text(encoding="utf-8")

    glossary_id = None
    if args.glossary.lower() != "none":
        name = f"epd-{args.src.upper()}-{args.tgt.upper()}" if args.glossary.lower() == "auto" else args.glossary
        try:
            glossary_id = find_glossary_id(deepl.Translator(os.environ["DEEPL_API_KEY"]), name, args.src, args.tgt)
            if glossary_id:
                print(f"Using glossary: {name} (id={glossary_id})")
            else:
                print(f"Note: glossary '{name}' not found for {args.src}->{args.tgt}; continuing without it.")
        except Exception as e:
            print(f"Warning: could not look up glossary: {e} (continuing without).")

    result = translate_html_with_deepl(
        raw,
        api_key=os.environ["DEEPL_API_KEY"],
        src=args.src,
        tgt=args.tgt,
        glossary=glossary_id,
        en_variant=args.en_variant,
    )
    outp.write_text(result, encoding="utf-8")
    print(f"Translated → {outp}")

if __name__ == "__main__":
    main()