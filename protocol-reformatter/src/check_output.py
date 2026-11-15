"""Check converted v011 .docx files against the v011 template.

Usage:
    python check_output.py [--template T] out/*.docx

Each docx is checked against the protocol JSON it was rendered from (<name>.generated.json for
<name>.docx, <name>.json for <name>.pre-llm.docx). Checks: headings match the JSON's sections, every
block's text is in the docx in order, figures are present, styles exist in the template, no
instructional text left, cross-references point at existing headings. Exits 1 if any check fails.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import docx
from docx.oxml.ns import qn

from converter.audit import build_audit, squash
from cross_references import OTHER_DOCUMENT, REFERENCE, replacements_for
from pdf_to_cpt_docx import STYLE_NAMES

DEFAULT_TEMPLATE = Path(__file__).resolve().parent.parent / "resources" / "CPT_CoreBWE-v011.docx"
INSTRUCTIONAL_STYLES = {"CPT_Instructional", "CPT_Example"}
MAX_SHOWN = 8
PRE_LLM_SUFFIX = ".pre-llm"


def output_outline(document):
    counters, outline = [], []
    heading_styles = STYLE_NAMES["v011"]["headings"]
    for paragraph in document.paragraphs:
        style = paragraph.style.name
        if style not in heading_styles:
            continue
        level = heading_styles.index(style) + 1
        del counters[level:]
        while len(counters) < level:
            counters.append(0)
        counters[-1] += 1
        outline.append((level, ".".join(map(str, counters)), paragraph.text.strip()))
    return outline


def check_outline(document, audit):
    actual = output_outline(document)
    if actual == audit.outline:
        return []
    problems = [f"{len(actual)} headings in docx, {len(audit.outline)} expected from the JSON"]
    for found, expected in zip(actual, audit.outline):
        if found != expected:
            problems.append(f"first heading mismatch: docx {found}, expected {expected}")
            break
    return problems


def check_styles(document, template):
    known = {style.name for style in template.styles}
    used = {p.style.name for p in document.paragraphs}
    for table in document.tables:
        used.add(table.style.name if table.style else "")
        for row in table.rows:
            for cell in row.cells:
                used.update(p.style.name for p in cell.paragraphs)
    problems = []
    unknown = sorted(name for name in used if name and name not in known)
    if unknown:
        problems.append(f"styles not in template: {unknown}")
    left = sorted(used & INSTRUCTIONAL_STYLES)
    if left:
        problems.append(f"instructional styles used: {left}")
    for paragraph in all_paragraphs(document):
        if re.search(r"<(Start|End) of (common|suggested|example) text>", paragraph.text):
            problems.append(f"template flag text left: {paragraph.text[:60]}")
            break
    return problems


def check_cross_references(document):
    numbers = {number for _, number, _ in output_outline(document)}
    bad = []
    for paragraph in all_paragraphs(document):
        for match in REFERENCE.finditer(paragraph.text):
            if OTHER_DOCUMENT.match(paragraph.text, match.end()):
                continue
            for replacement in replacements_for(match):
                for number in replacement.source_numbers:
                    if number not in numbers:
                        bad.append(f"{match.group(0)} (missing {number})")
    return [f"{len(bad)} references to missing sections, e.g. {bad[:MAX_SHOWN]}"] if bad else []


def all_paragraphs(document):
    yield from document.paragraphs
    for table in document.tables:
        seen = set()
        for row in table.rows:
            for cell in row.cells:
                if cell._tc not in seen:
                    seen.add(cell._tc)
                    yield from cell.paragraphs


def check_content(document, audit):
    body_text = squash("".join(text.text or "" for text in document.element.body.iter(qn("w:t"))))
    position = 0
    missing = []
    for expected in audit.blocks:
        found_at = body_text.find(expected.text, position)
        if found_at < 0:
            missing.append(f"{expected.kind} in {expected.where}: {expected.text[:40]!r}")
        else:
            position = found_at + len(expected.text)
    problems = []
    if missing:
        problems.append(f"{len(missing)} blocks missing or out of order, e.g. {missing[:MAX_SHOWN]}")
    figures = len(list(document.element.body.iter(qn("w:drawing"))))
    if figures < audit.figures:
        problems.append(f"{audit.figures} figures in the JSON, {figures} in the docx")
    return problems


def protocol_json_path(docx_path: Path) -> Path:
    if docx_path.stem.endswith(PRE_LLM_SUFFIX):
        return docx_path.with_name(docx_path.stem.removesuffix(PRE_LLM_SUFFIX) + ".json")
    return docx_path.with_name(docx_path.stem + ".generated.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("outputs", nargs="+", type=Path)
    args = parser.parse_args()
    template = docx.Document(str(args.template))
    failed = False
    for path in args.outputs:
        document = docx.Document(str(path))
        json_path = protocol_json_path(path)
        if json_path.exists():
            audit = build_audit(json.loads(json_path.read_text(encoding="utf-8")))
            content_problems = check_outline(document, audit) + check_content(document, audit)
        else:
            content_problems = [f"missing protocol JSON {json_path.name}: cannot verify content"]
        problems = content_problems + check_styles(document, template) + check_cross_references(document)
        status = "FAIL" if problems else "ok"
        failed |= bool(problems)
        print(f"{status}  {path.name}  (headings: {len(output_outline(document))})")
        for problem in problems:
            print(f"      {problem}")
        report_path = path.with_suffix(".report.txt")
        if report_path.exists() and not path.stem.endswith(PRE_LLM_SUFFIX):
            text = report_path.read_text(encoding="utf-8").split("\nOutput verification\n")[0]
            text += "\nOutput verification\n" + ("\n".join(problems) if problems else "All automated DOCX checks passed") + "\n"
            if any("references to missing sections" in problem for problem in problems):
                text += "Unresolved references remain for review: no replacement target was guessed.\n"
            text += "Manual check still required: open in Word, update the TOC, and inspect pagination and layout against the v011 examples.\n"
            report_path.write_text(text, encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
