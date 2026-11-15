"""Read a v010 protocol PDF into the shared Document model."""

import re
from pathlib import Path

import pymupdf

from pdf_to_cpt_docx import (
    REDACTED_TITLES, Document, Figure, Heading, body_font_size, classify_paragraphs, collapse_redactions, join_across_pages,
    merge_split_tables, read_pdf, structure,
)

BOOKMARK_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)(?:\.?\s|$)")


def numbered_bookmarks(path: Path) -> list[tuple[str, str, int]]:
    """(number, title, page) for each PDF bookmark that starts with a section number."""
    with pymupdf.open(path) as pdf:
        bookmarks = pdf.get_toc()
    rows = []
    for _, text, page in bookmarks:
        match = BOOKMARK_NUMBER.match(text)
        if match:
            rows.append((match.group(1), text[match.end():].strip(), page))
    return rows


def load_pdf(path: Path) -> Document:
    items, toc_pages = read_pdf(str(path))
    document = structure(items, toc_pages, body_font_size(items), numbered_bookmarks(path))
    for part in ("front", "body"):
        blocks = collapse_redactions(merge_split_tables(join_across_pages(getattr(document, part))))
        classify_paragraphs(blocks)
        setattr(document, part, blocks)
    return document


def same_heading_title(detected, bookmarked):
    if detected in REDACTED_TITLES and bookmarked in REDACTED_TITLES:
        return True
    return re.sub(r"\W+", "", detected).casefold() == re.sub(r"\W+", "", bookmarked).casefold()


def source_checks(path, document):
    headings = [block for block in document.body if isinstance(block, Heading)]
    scanned_pages = sorted(block.page for block in document.body + document.front
                           if isinstance(block, Figure) and block.full_page)
    detected = {heading.number: heading for heading in headings}
    mismatches, unreadable = [], []
    for number, title, page in numbered_bookmarks(path):
        heading = detected.get(number)
        label = f"bookmark {number} {title} p{page}"
        if page in scanned_pages:
            unreadable.append(f"{label}: on scanned page, not checkable")
        elif not heading or heading.page != page:
            mismatches.append(f"{label}: detected " + (f"p{heading.page}" if heading else "not found"))
        elif not same_heading_title(heading.title, title):
            mismatches.append(f"{label}: detected title {heading.title!r}")
    with pymupdf.open(path) as pdf:
        bookmark_count = len(pdf.get_toc())
    return {"bookmarks": bookmark_count, "bookmark_mismatches": mismatches,
            "bookmarks_on_scanned_pages": unreadable, "scanned_pages": scanned_pages}
