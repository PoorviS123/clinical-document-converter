"""Check that load_docx kept every visible passage of a v010 .docx.

Collects the visible text of document.xml (and footnotes/endnotes) independently of the reader, then
compares it with the blocks load_docx returns, in three passes:

1. Long passages: every source paragraph of MIN_WORDS or more words must appear in the text of the Document.
2. Container-aware, all lengths: every non-empty source paragraph (1 word and up) must appear in the
   Document block that should hold it. Body and front-matter paragraphs are searched in reading order;
   table cells are compared with the Document table's grid cell by cell (same row and column after merges).
3. Title page: every source label, value and table cell before the front matter must appear in the title tables.

Anything missing is either "intentionally dropped" (template guidance, placeholders, markers, the TOC,
title-page items the reader reports in its notes) or "unexplained". The target is zero unexplained.

Usage: python docx_coverage.py SOURCE.docx [--template TEMPLATE.docx]
"""

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import docx
from docx.oxml.ns import qn
from docx.table import Table as SourceTable
from docx.text.paragraph import Paragraph as SourceParagraph

from converter.extract import docx as reader_module
from converter.extract.docx import (
    DATE_ONLY, DEFAULT_TEMPLATE, FRONT_HEADING_STYLE, GUIDANCE_MARKER, MIN_GUIDANCE_WORDS, NUMBERED_HEADING,
    PAGE_TITLE_STYLE, PLACEHOLDER_ONLY, TYPED_NUMBER, is_template_guidance, template_guidance,
)
from pdf_to_cpt_docx import COVER_HEADING, SKIPPED_FRONT_MATTER, Document, FrontHeading, Heading, Paragraph, Subheading, Table

MIN_WORDS = 5
MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
NOTE_TYPES_SKIPPED = ("separator", "continuationSeparator", "continuationNotice")
SENTENCE_BREAK = re.compile(r"(?<=[.!?;:])\s+")
TITLE_CELL_WORDS_EXPLAINED = {"date"}  # the signature block's caption; its name is folded into Sponsor Signatory


def squash(text: str) -> str:
    """Letters and digits only, casefolded: robust to spacing, tabs and joined paragraphs."""
    return re.sub(r"[\W_]+", "", text.casefold())


def ancestors(element) -> set[str]:
    return {a.tag.rsplit("}", 1)[-1] for a in element.iterancestors()}


def visible_texts(root, stack: list[str] | None = None) -> dict:
    """{paragraph element: visible text}, in document order, excluding deletions, field codes and mc:Fallback."""
    stack = [] if stack is None else stack
    out: dict = defaultdict(str)
    for node in root.iter(qn("w:fldChar"), qn("w:t"), f"{{{MATH_NS}}}t"):
        if node.tag == qn("w:fldChar"):
            kind = node.get(qn("w:fldCharType"))
            if kind == "begin":
                stack.append("code")
            elif kind == "separate" and stack:
                stack[-1] = "result"
            elif kind == "end" and stack:
                stack.pop()
            continue
        if ancestors(node) & {"del", "moveFrom", "Fallback"} or any(state == "code" for state in stack):
            continue
        paragraph = next((a for a in node.iterancestors() if a.tag == qn("w:p")), None)
        if paragraph is not None:
            out[paragraph] += node.text or ""
    order = {p: i for i, p in enumerate(root.iter(qn("w:p")))}
    return dict(sorted(out.items(), key=lambda item: order.get(item[0], 0)))


def note_roots(document) -> list:
    roots = []
    for rel in document.part.rels.values():
        if rel.reltype.endswith(("/footnotes", "/endnotes")):
            for note in docx.oxml.parse_xml(rel.target_part.blob):
                if note.get(qn("w:type")) not in NOTE_TYPES_SKIPPED:
                    roots.append(note)
    return roots


@dataclass
class DocumentText:
    ordered: list[str]  # squashed text of front/body paragraph-like blocks, in order
    tables: list[Table]  # front and body tables, in order
    title: str  # squashed text of all title tables
    everything: str  # squashed text of every block


def document_text(result: Document) -> DocumentText:
    ordered, tables, everything = [], [], []

    def table_texts(table: Table) -> list[str]:
        return [p.text for row in table.grid for cell in row if cell for p in cell["paragraphs"]]

    for part in (result.front, result.body):
        for block in part:
            if isinstance(block, Paragraph):
                ordered.append(squash(block.text))
            elif isinstance(block, (Heading, FrontHeading, Subheading)):
                ordered.append(squash(block.title))
            elif isinstance(block, Table):
                tables.append(block)
                everything.extend(table_texts(block))
    everything.extend(texts for texts in (squash(t) for t in ordered))
    title = " ".join(text for table in result.title_tables for text in table_texts(table))
    return DocumentText(ordered, tables, squash(title), squash(" ".join(everything)) + squash(title))


def paragraph_style(paragraph, document) -> str:
    style = SourceParagraph(paragraph, document).style
    return (style.name if style is not None else "").casefold()


def classify(paragraph, text: str, style: str, guidance: frozenset[str]) -> str:
    """Why a paragraph that is absent from the Document is allowed to be: '' when nothing explains it."""
    if GUIDANCE_MARKER.match(text):
        return "start/end marker"
    if SKIPPED_FRONT_MATTER.match(text.strip()):
        return "table of contents"
    if PLACEHOLDER_ONLY.match(text.strip()):
        return "placeholder-only"
    if "instructional" in style and "tc" not in ancestors(paragraph) and len(text.split()) >= MIN_GUIDANCE_WORDS \
            and is_template_guidance(text, guidance):
        return "template guidance"
    in_toc = paragraph.xpath("boolean(ancestor::w:sdt/w:sdtPr/w:docPartObj/w:docPartGallery[@w:val='Table of Contents'])")
    if style.startswith(("toc", "table of figures")) or in_toc:
        return "table of contents"
    return ""


def is_title_end(style: str, text: str) -> bool:
    heading = NUMBERED_HEADING.match(style) or FRONT_HEADING_STYLE.match(style) or style == PAGE_TITLE_STYLE
    return bool(heading and text.strip() and not COVER_HEADING.match(text.strip()))


@dataclass
class Report:
    total: int = 0
    checked: Counter = field(default_factory=Counter)  # what pass 2 and 3 compared: paragraphs, cells, title items
    dropped: list[tuple[str, str]] = field(default_factory=list)
    unexplained: list[str] = field(default_factory=list)
    out_of_order: list[str] = field(default_factory=list)
    cell_misses: list[str] = field(default_factory=list)
    title_misses: list[str] = field(default_factory=list)

    @property
    def unexplained_total(self) -> int:
        return len(self.unexplained) + len(self.cell_misses) + len(self.title_misses)


def table_cells(table: SourceTable):
    """(row, column, tc element) for each cell once, at the grid position where it starts."""
    seen = set()
    for r, row in enumerate(table.rows):
        for c, cell in enumerate(row.cells):
            if cell._tc not in seen:
                seen.add(cell._tc)
                yield r, c, cell._tc


def top_level_elements(body) -> list:
    out = []
    for child in body:
        name = child.tag.rsplit("}", 1)[-1]
        if name in ("p", "tbl"):
            out.append(child)
        elif name == "sdt":
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                out.extend(c for c in content if c.tag in (qn("w:p"), qn("w:tbl")))
    return out


def check(path: Path, template: Path = DEFAULT_TEMPLATE) -> Report:
    source = docx.Document(str(path))
    result, _ = reader_module.read_docx(path, template)
    text = document_text(result)
    guidance = template_guidance(template)
    report = Report()
    paragraphs = visible_texts(source.element.body)
    notes = {}
    for note in note_roots(source):
        notes.update(visible_texts(note))

    def explained(paragraph, content: str) -> str:
        return classify(paragraph, content, paragraph_style(paragraph, source), guidance)

    # Pass 1: long passages anywhere in the Document.
    for paragraph, raw in {**paragraphs, **notes}.items():
        content = " ".join(raw.split())
        if len(content.split()) < MIN_WORDS:
            continue
        report.total += 1
        style = paragraph_style(paragraph, source) if paragraph in paragraphs else ""
        probe = TYPED_NUMBER.sub("", content) if "heading" in style else content
        if squash(probe) in text.everything:
            continue
        reason = explained(paragraph, content) if paragraph in paragraphs else ""
        if reason:
            report.dropped.append((reason, content))
            continue
        report.unexplained.extend(s for s in SENTENCE_BREAK.split(content)
                                  if len(s.split()) >= MIN_WORDS and squash(s) not in text.everything)

    # Pass 2 and 3: walk the top-level elements, split at the end of the title page.
    in_title = True
    cursor, table_cursor = 0, 0
    for element in top_level_elements(source.element.body):
        if element.tag == qn("w:p"):
            content = " ".join(visible_texts(element).get(element, "").split())
            style = paragraph_style(element, source)
            if in_title and is_title_end(style, content):
                in_title = False
            if not content:
                continue
            if in_title:
                check_title_text(report, text, content)
                continue
            if explained(element, content):
                continue
            probe = TYPED_NUMBER.sub("", content) if "heading" in style else content
            cursor = check_in_order(report, text, probe, cursor)
            for textbox in element.iter(qn("w:txbxContent")):
                for inner, raw in visible_texts(textbox).items():
                    inner_text = " ".join(raw.split())
                    if inner_text and not explained(inner, inner_text):
                        cursor = check_in_order(report, text, inner_text, cursor)
        else:
            table = SourceTable(element, source)
            if in_title:
                for row in visible_texts(element).values():
                    check_title_text(report, text, " ".join(row.split()))
                continue
            table_cursor = check_table(report, text, table, table_cursor, explained)
    for paragraph, raw in notes.items():
        content = " ".join(raw.split())
        if content and squash(content) not in text.everything:
            report.unexplained.append(content)
    return report


def check_title_text(report: Report, text: DocumentText, content: str) -> None:
    report.checked["title items"] += 1
    if COVER_HEADING.match(content) or content.casefold() in TITLE_CELL_WORDS_EXPLAINED or DATE_ONLY.match(content):
        return
    if squash(content) not in text.title:
        report.title_misses.append(content)


def check_in_order(report: Report, text: DocumentText, content: str, cursor: int) -> int:
    report.checked["body paragraphs"] += 1
    probe = squash(content)
    short = len(content.split()) < MIN_WORDS

    def matches(block: str) -> bool:
        return block == probe or block.startswith(probe) or block.endswith(probe) if short else probe in block

    for index in range(cursor, len(text.ordered)):
        if matches(text.ordered[index]):
            return index
    if any(matches(block) for block in text.ordered):
        report.out_of_order.append(content)
    else:
        report.unexplained.append(content)
    return cursor


def check_table(report: Report, text: DocumentText, table: SourceTable, cursor: int, explained) -> int:
    shape = (len(table.rows), len(table.columns))
    match = next((i for i in range(cursor, len(text.tables))
                  if (len(text.tables[i].grid), len(text.tables[i].col_widths)) == shape), None)
    if match is None:
        report.cell_misses.append(f"table {shape[0]}x{shape[1]} starting '{table.rows[0].cells[0].text[:30]}' not found in order")
        return cursor
    grid = text.tables[match].grid
    for r, c, tc in table_cells(table):
        cell = grid[r][c] if r < len(grid) and c < len(grid[r]) else None
        doc_cell = squash(" ".join(p.text for p in cell["paragraphs"])) if cell else None
        for inner, raw in visible_texts(tc).items():
            content = " ".join(raw.split())
            if not content or explained(inner, content):
                continue
            report.checked["cell paragraphs"] += 1
            if doc_cell is None:
                report.cell_misses.append(f"table {match} cell ({r},{c}) is missing: '{content[:40]}'")
            elif squash(content) not in doc_cell:
                report.cell_misses.append(f"table {match} cell ({r},{c}) lacks '{content[:40]}'")
    return match + 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    args = parser.parse_args()
    report = check(args.source, args.template)
    print(f"{report.total} source paragraphs of {MIN_WORDS}+ words checked (headers and footers are not in document.xml)")
    print("also compared container by container: " + ", ".join(f"{n} {what}" for what, n in report.checked.items()))
    print(f"intentionally dropped: {len(report.dropped)}")
    for reason, content in report.dropped:
        print(f"  [{reason}] {content[:90]}")
    for label, items in (("long passages missing", report.unexplained), ("table cells missing", report.cell_misses),
                         ("title-page values missing", report.title_misses)):
        print(f"UNEXPLAINED {label}: {len(items)}")
        for item in items:
            print(f"  - {item[:160]}")
    print(f"found but out of reading order: {len(report.out_of_order)}")
    for item in report.out_of_order:
        print(f"  ~ {item[:120]}")
    print(f"UNEXPLAINED TOTAL: {report.unexplained_total}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
