"""Extract Section 1 (Protocol Summary) of a CPT v10 protocol to JSON that follows the v10 hierarchy.

Section 1 -> subsections (1.1 Synopsis, 1.2 Schema, 1.3 SoA) -> synopsis fields -> content blocks.
Every block keeps its `source_index` (position among the v10 body elements) so the insert step can
copy the original formatting, tables and images. Anything expected but not found, or found but not
readable with confidence, is set to UNSURE.

Usage: python extract_v10_section1.py <v10 protocol.docx> [--output <section1.json>]
"""

import argparse
import json
import re
from pathlib import Path

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from lxml.etree import _Element

from convert_v10_title_page import Table, table_rows, visible_text

UNSURE = "__UNSURE__"

SECTION_HEADING_STYLE = "Heading 1"
SUBSECTION_HEADING_STYLE = "Heading 2"
NESTED_HEADING_STYLES = ("Heading 3", "Heading 4")
SECTION_NUMBER = "1"
SECTION_TITLE_PATTERN = re.compile(r"protocol summary", re.IGNORECASE)
FIELD_LABEL_STYLE = "Heading (No TOC)"
INSTRUCTIONAL_STYLE = "CPT_Instructional"
EXAMPLE_MARKER_PATTERN = re.compile(r"^<(start|end) of .*>$", re.IGNORECASE)
TEMPLATE_PLACEHOLDER_PATTERN = re.compile(r"^\[[^\]]*\]$")
IMAGE_TAGS = (qn("w:drawing"), qn("w:pict"), qn("w:object"))
LABEL_SEPARATOR = ":"

SUBSECTION_PATTERNS = {
    "synopsis": re.compile(r"synopsis", re.IGNORECASE),
    "schema": re.compile(r"schema", re.IGNORECASE),
    "schedule_of_activities": re.compile(r"schedule of activities", re.IGNORECASE),
}

# Synopsis field labels. Matched loosely: real protocols reword them (eg "Objectives, Endpoints,
# Assessments", "Study Arms/Sequences and Duration", "Data Safety Monitoring Committee").
SYNOPSIS_FIELD_PATTERNS = {
    "protocol_title": re.compile(r"^\[?protocol title", re.IGNORECASE),
    "brief_title": re.compile(r"^\[?brief title", re.IGNORECASE),
    "regulatory_identifiers": re.compile(r"^\[?regulatory agency identifier", re.IGNORECASE),
    "pediatric_plan_number": re.compile(r"^\[?pediatric investigational plan", re.IGNORECASE),
    "rationale": re.compile(r"^\[?rationale", re.IGNORECASE),
    "objectives": re.compile(r"^\[?objectives", re.IGNORECASE),
    "overall_design": re.compile(r"^\[?overall design", re.IGNORECASE),
    "brief_summary": re.compile(r"^\[?brief summary", re.IGNORECASE),
    "number_of_participants": re.compile(r"^\[?number of participants", re.IGNORECASE),
    "study_arms_and_duration": re.compile(r"^\[?study arms", re.IGNORECASE),
    "committees": re.compile(r"committee", re.IGNORECASE),
}
# The v10 template sets "Brief Summary:" in Normal instead of the label style.
UNSTYLED_LABEL_PATTERN = re.compile(r"^brief summary\s*:", re.IGNORECASE)

# Values in the v10 Brief Summary "Study details include:" bullets.
BRIEF_SUMMARY_VALUE_PATTERNS = {
    "study_duration": re.compile(r"study duration will be (?:up to )?(?P<value>[^.]+)", re.IGNORECASE),
    "treatment_duration": re.compile(
        r"(?:treatment duration|intervention periods?) will be (?:up to )?(?P<value>[^.]+)", re.IGNORECASE
    ),
    "visit_frequency": re.compile(r"visit frequency will be (?P<value>[^.]+)", re.IGNORECASE),
}

OBJECTIVE_CATEGORY_PATTERNS = {
    "primary": re.compile(r"^primary", re.IGNORECASE),
    "secondary": re.compile(r"^(key )?secondary", re.IGNORECASE),
    "exploratory": re.compile(r"^(tertiary|exploratory|other)", re.IGNORECASE),
}
OBJECTIVE_COLUMN_KEYWORDS = {"objective": "objective", "endpoint": "endpoint", "estimand": "estimand"}

Block = dict[str, object]
Node = dict[str, object]


def paragraph_style(document: DocumentObject, element: _Element) -> str:
    style = Paragraph(element, document).style
    return style.name if style is not None else ""


def has_image(element: _Element) -> bool:
    return any(True for _ in element.iter(*IMAGE_TAGS))


def is_placeholder(text: str) -> bool:
    return bool(TEMPLATE_PLACEHOLDER_PATTERN.match(text))


def section_one_elements(document: DocumentObject) -> list[tuple[int, _Element]]:
    children = list(document.element.body)
    start = None
    for index, element in enumerate(children):
        if element.tag != qn("w:p") or paragraph_style(document, element) != SECTION_HEADING_STYLE:
            continue
        if start is None and SECTION_TITLE_PATTERN.search(visible_text(element)):
            start = index
        elif start is not None:
            return list(enumerate(children))[start:index]
    if start is None:
        raise SystemExit("No 'Protocol Summary' Heading 1 found")
    return list(enumerate(children))[start:]


def paragraph_block(document: DocumentObject, index: int, element: _Element) -> Block | None:
    style = paragraph_style(document, element)
    text = visible_text(element).strip()
    if style == INSTRUCTIONAL_STYLE or EXAMPLE_MARKER_PATTERN.match(text):
        return None
    image = has_image(element)
    if not text and not image:
        return None
    block: Block = {"type": "paragraph", "style": style, "text": text, "source_index": index}
    if image:
        block["has_image"] = True
    return block


def table_block(index: int, element: _Element) -> Block:
    return {"type": "table", "rows": table_rows(element), "source_index": index}


def label_field(index: int, element: _Element) -> Node:
    """A synopsis label like "Number of Participants: 70". Text after the colon is the inline value."""
    raw_text = visible_text(element)
    label, separator, after = raw_text.partition(LABEL_SEPARATOR)
    if not separator:
        label, after = raw_text, ""
    inline_value = after.strip()
    return {
        "label": label.strip().strip("[]"),
        "label_source_index": index,
        # Characters to drop from the start of the label paragraph to leave only the inline value.
        "label_length": len(raw_text) - len(after.lstrip()) if separator else len(raw_text),
        "inline_value": UNSURE if is_placeholder(inline_value) else inline_value,
        "content": [],
    }


def new_node(number: str, heading: str, index: int) -> Node:
    return {"number": number, "heading": heading, "source_index": index, "content": []}


def parse_hierarchy(document: DocumentObject) -> tuple[Node, list[Node]]:
    """Walk Section 1 and return the section node and its Heading 2 subsections in document order."""
    elements = section_one_elements(document)
    heading_index, heading_element = elements[0]
    section = new_node(SECTION_NUMBER, visible_text(heading_element).strip(), heading_index)
    subsections: list[Node] = []
    current: Node = section
    current_field: Node | None = None

    for index, element in elements[1:]:
        block: Block | None
        if element.tag == qn("w:tbl"):
            block = table_block(index, element)
        elif element.tag == qn("w:p"):
            style = paragraph_style(document, element)
            text = visible_text(element).strip()
            if style == SUBSECTION_HEADING_STYLE:
                current = new_node(f"{SECTION_NUMBER}.{len(subsections) + 1}", text, index)
                subsections.append(current)
                current_field = None
                continue
            in_synopsis = SUBSECTION_PATTERNS["synopsis"].search(str(current["heading"]))
            if in_synopsis and (style == FIELD_LABEL_STYLE or UNSTYLED_LABEL_PATTERN.match(text)) and text:
                current_field = label_field(index, element)
                current.setdefault("raw_fields", []).append(current_field)
                continue
            if style in NESTED_HEADING_STYLES:
                block = {"type": "heading", "style": style, "text": text, "source_index": index}
                current_field = None
            else:
                block = paragraph_block(document, index, element)
        else:
            continue
        if block is not None:
            target = current_field if current_field is not None else current
            target["content"].append(block)
    return section, subsections


def match_by_pattern(items: list[Node], text_key: str, patterns: dict[str, re.Pattern[str]]) -> tuple[dict, list]:
    """Assign each item to the first unused pattern its text matches. Unmatched keys get UNSURE."""
    matched: dict[str, Node | str] = {key: UNSURE for key in patterns}
    unrecognized = []
    for item in items:
        key = next(
            (k for k, pattern in patterns.items() if matched[k] == UNSURE and pattern.search(str(item[text_key]))),
            None,
        )
        if key is None:
            unrecognized.append(item)
        else:
            matched[key] = item
    return matched, unrecognized


def objective_rows(table: Table) -> list[dict[str, object]] | str:
    header, *rows = table
    columns = {}
    for name, keyword in OBJECTIVE_COLUMN_KEYWORDS.items():
        columns[name] = next((i for i, cell in enumerate(header) if keyword in cell.lower()), None)
    if columns["objective"] is None:
        return UNSURE
    known_columns = {i for i in columns.values() if i is not None}

    parsed = []
    category: str = UNSURE
    category_label: str = UNSURE
    for row in rows:
        filled = [cell for cell in row if cell]
        if len(filled) == 1:
            label = filled[0]
            category = next((k for k, p in OBJECTIVE_CATEGORY_PATTERNS.items() if p.match(label)), UNSURE)
            category_label = label
            continue

        def cell(name: str) -> str:
            column = columns[name]
            if column is None or column >= len(row) or not row[column]:
                return UNSURE
            return row[column]

        other_columns = {
            header[i]: value for i, value in enumerate(row) if i not in known_columns and i < len(header) and value
        }
        parsed.append(
            {
                "category": category,
                "category_label": category_label,
                "objective": cell("objective"),
                "endpoint": cell("endpoint"),
                "estimand": cell("estimand"),
                "other_columns": other_columns,
            }
        )
    return parsed


def first_table(field: Node) -> Table | None:
    return next((block["rows"] for block in field["content"] if block["type"] == "table"), None)


def brief_summary_values(field: Node) -> dict[str, str]:
    texts = [str(field["inline_value"])] + [str(block.get("text", "")) for block in field["content"]]
    values = {}
    for key, pattern in BRIEF_SUMMARY_VALUE_PATTERNS.items():
        match = next((m for m in (pattern.search(text) for text in texts) if m), None)
        value = match.group("value").strip() if match else ""
        values[key] = value if value and not value.startswith("[") else UNSURE
    return values


def build_synopsis(node: Node) -> Node:
    fields, unrecognized = match_by_pattern(node.pop("raw_fields", []), "label", SYNOPSIS_FIELD_PATTERNS)
    objectives = fields["objectives"]
    if objectives != UNSURE:
        table = first_table(objectives)
        objectives["rows"] = objective_rows(table) if table else UNSURE
    brief_summary = fields["brief_summary"]
    if brief_summary != UNSURE:
        brief_summary["values"] = brief_summary_values(brief_summary)
    node["fields"] = fields
    node["unrecognized_fields"] = unrecognized
    return node


def extract(v10_path: Path) -> dict[str, object]:
    document = Document(str(v10_path))
    section, found_subsections = parse_hierarchy(document)
    subsections, unrecognized = match_by_pattern(found_subsections, "heading", SUBSECTION_PATTERNS)
    if subsections["synopsis"] != UNSURE:
        build_synopsis(subsections["synopsis"])
    section["subsections"] = subsections
    section["unrecognized_subsections"] = unrecognized
    return {
        "source_path": str(v10_path.resolve()),
        "source_template": "CPT v010",
        "unsure_value": UNSURE,
        "section_1": section,
    }


def unsure_paths(value: object, path: str = "") -> list[str]:
    if value == UNSURE:
        return [path]
    if isinstance(value, dict):
        return [found for key, child in value.items() for found in unsure_paths(child, f"{path}.{key}")]
    if isinstance(value, list):
        return [found for i, child in enumerate(value) for found in unsure_paths(child, f"{path}[{i}]")]
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("v10_protocol", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    output_path = args.output or Path(__file__).resolve().parent / f"{args.v10_protocol.stem}_section1.json"
    extracted = extract(args.v10_protocol)
    output_path.write_text(json.dumps(extracted, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {output_path}")
    unsure = unsure_paths(extracted["section_1"], "section_1")
    print(f"{len(unsure)} value(s) set to {UNSURE}:")
    for path in unsure:
        print(f"  {path.lstrip('.')}")


if __name__ == "__main__":
    main()
