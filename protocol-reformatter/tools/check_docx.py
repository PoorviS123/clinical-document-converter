"""Look for structural problems that make Word refuse to open a .docx.

Usage: python check_docx.py <file.docx> [<file.docx> ...]
"""

import sys
import zipfile
from pathlib import Path

from lxml import etree

WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def field_problems(document: etree._Element) -> list[str]:
    problems = []
    depth = 0
    for marker in document.iter(f"{WORD_NS}fldChar"):
        marker_type = marker.get(f"{WORD_NS}fldCharType")
        if marker_type == "begin":
            depth += 1
        elif depth == 0:
            problems.append(f"field '{marker_type}' marker with no 'begin' before it")
        elif marker_type == "end":
            depth -= 1
    if depth:
        problems.append(f"{depth} field(s) never closed with an 'end' marker")
    return problems


def bookmark_problems(document: etree._Element) -> list[str]:
    starts = {element.get(f"{WORD_NS}id") for element in document.iter(f"{WORD_NS}bookmarkStart")}
    ends = {element.get(f"{WORD_NS}id") for element in document.iter(f"{WORD_NS}bookmarkEnd")}
    unmatched = sorted(starts ^ ends)
    return [f"bookmark ids without a matching start/end: {unmatched}"] if unmatched else []


def table_problems(document: etree._Element) -> list[str]:
    empty_cells = [
        cell
        for cell in document.iter(f"{WORD_NS}tc")
        if cell.find(f"{WORD_NS}p") is None and cell.find(f"{WORD_NS}tbl") is None
    ]
    return [f"{len(empty_cells)} table cell(s) with no paragraph"] if empty_cells else []


def check(docx_path: Path) -> list[str]:
    problems = []
    with zipfile.ZipFile(docx_path) as archive:
        for part_name in archive.namelist():
            if part_name.endswith((".xml", ".rels")):
                try:
                    etree.fromstring(archive.read(part_name))
                except etree.XMLSyntaxError as error:
                    problems.append(f"{part_name} is not valid XML: {error}")
        document = etree.fromstring(archive.read("word/document.xml"))
    return problems + field_problems(document) + bookmark_problems(document) + table_problems(document)


def main() -> None:
    found_problems = False
    for argument in sys.argv[1:]:
        problems = check(Path(argument))
        print(f"{argument}: {'OK' if not problems else ''}")
        for problem in problems:
            print(f"  {problem}")
        found_problems = found_problems or bool(problems)
    sys.exit(1 if found_problems else 0)


if __name__ == "__main__":
    main()
