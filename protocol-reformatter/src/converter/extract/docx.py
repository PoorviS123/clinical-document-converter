"""Read a v010 protocol .docx into the shared Document model.

Walks the body in order and produces the same blocks load_pdf does, so everything downstream
(place, render, title page) is unchanged. Word has no pages: `page` on every block is the 1-based
index of the Word section it sits in.
"""

import difflib
import logging
import math
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import docx
import pymupdf
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from lxml.etree import _Element

from pdf_to_cpt_docx import (
    CAPTION, COVER_HEADING, DEFAULT_CELL_PADDING, DEFAULT_TABLE_FONT_SIZE, FRONT_MATTER_HEADINGS,
    MAX_CAPTION_LENGTH, MISSING_HEADING_TITLE, SKIPPED_FRONT_MATTER, TOC_LEADER, Document, Figure, FrontHeading, Heading, PageMark,
    Paragraph, Span, Subheading, Table, advance, canonical_label, split_at_labels, split_label, tidy_spans,
)

log = logging.getLogger(__name__)

TWIPS_PER_PT = 20
EMU_PER_PT = 12700
HALF_POINTS_PER_PT = 2
DEFAULT_FONT_SIZE = 11.0
DEFAULT_PAGE_SIZE_TWIPS = (12240, 15840)
DEFAULT_MARGIN_TWIPS = 1440
LINE_HEIGHT_FACTOR = 1.2
AVERAGE_CHAR_WIDTH_FACTOR = 0.5  # of the font size
TITLE_LABEL_COLUMN_PT = 180.0
TITLE_VALUE_COLUMN_PT = 288.0
MAX_STYLE_DEPTH = 20
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MEDICAL_MONITOR_LABEL = "Medical Monitor Name and Contact Information:"
MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
MATH_PROPERTY_ELEMENTS = {"rPr", "ctrlPr", "oMathParaPr", "fPr", "radPr", "naryPr", "dPr", "accPr", "barPr", "boxPr",
                          "eqArrPr", "groupChrPr", "limLowPr", "limUppPr", "mPr", "phantPr", "sPrePr", "sSubPr",
                          "sSubSupPr", "sSupPr", "borderBoxPr", "funcPr"}
NOTE_KINDS = ("footnote", "endnote")
NOTE_SEPARATOR_TYPES = ("separator", "continuationSeparator", "continuationNotice")
NOTE_NUMBER_FORMATS = {"footnote": "decimal", "endnote": "lowerRoman"}  # Word's defaults
FIGURE_MARKER = "[Figure]"

# Style names are compared case-folded; the numbered ones come as "Heading 2" or "02Heading 2".
NUMBERED_HEADING = re.compile(r"^(?:0\d\s?)?heading ([1-9])$")
FRONT_HEADING_STYLE = re.compile(r"^heading [1-9] \((?:non-numbered|no toc)\)$|^(?:0\d\s?)?subheading bold$")
PAGE_TITLE_STYLE = "heading 1 (no toc)"
LABEL_STYLE = "heading (no toc)"
# CPT_Example is template wording that authors adopt as their own, so only CPT_Instructional is filtered.
GUIDANCE_STYLES = {"cpt_instructional"}
PLACEHOLDER_ONLY = re.compile(r"^\[[^\]]*\]$")
# Sponsors often type their own protocol text over the guidance and leave the style on, so a
# CPT_Instructional paragraph is dropped only when the v010 template contains it.
DEFAULT_TEMPLATE = Path(__file__).resolve().parents[3] / "resources" / "CPT_CoreBWE_v010.docx"
GUIDANCE_MATCH_RATIO = 0.9  # difflib ratio at which a paragraph counts as the template's text
MIN_GUIDANCE_WORDS = 3
DROPPED_PREVIEW_LENGTH = 70
DASHES = str.maketrans({"–": "-", "—": "-", "‑": "-", "−": "-", "‘": "'", "’": "'",
                        "“": '"', "”": '"', " ": " "})
DATE_ONLY = re.compile(r"^\W*(\d{1,2}[-./ ][A-Za-z]+[-./ ]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{2,4})\W*$")
GUIDANCE_MARKER = re.compile(r"^\s*<(start|end) of .*>\s*$", re.IGNORECASE)
BULLET_STYLE = re.compile(r"list\s?(indent)?\s?bull|bullet")
TOC_STYLE = re.compile(r"^(toc \d|table of figures)")
TYPED_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?[\s ]+(?=\S)")
CONTINUATION_BLOCKERS = ("medical monitor",)

ROMAN = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
         (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
LETTER_COUNT = 26


def local(element: _Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def attr(element: _Element | None, name: str, default: str | None = None) -> str | None:
    if element is None:
        return default
    return element.get(qn(f"w:{name}"), default)


def flag(element: _Element | None) -> bool | None:
    """Value of a w:b-style toggle element: None when absent."""
    if element is None:
        return None
    return attr(element, "val", "true").lower() not in ("false", "0", "off")


def twips_to_pt(value: str | None) -> float:
    return int(value) / TWIPS_PER_PT if value and value.lstrip("-").isdigit() else 0.0


# ---------------------------------------------------------------- styles and numbering

class Styles:
    def __init__(self, document: docx.document.Document) -> None:
        root = document.styles.element
        self.by_id = {style.get(qn("w:styleId")): style for style in root.findall(qn("w:style"))}
        defaults = root.find(qn("w:docDefaults"))
        self.default_rpr = None if defaults is None else defaults.find(f"{qn('w:rPrDefault')}/{qn('w:rPr')}")

    def name(self, style_id: str | None) -> str:
        style = self.by_id.get(style_id)
        name = attr(style.find(qn("w:name")), "val", "") if style is not None else ""
        return re.sub(r"\s+", " ", name).strip().casefold()

    def chain(self, style_id: str | None) -> Iterator[_Element]:
        for _ in range(MAX_STYLE_DEPTH):
            style = self.by_id.get(style_id)
            if style is None:
                return
            yield style
            style_id = attr(style.find(qn("w:basedOn")), "val")

    def lookup(self, style_ids: list[str | None], container: str, path: str) -> _Element | None:
        """First element at `path` inside the style's pPr/rPr, searching each style chain in order."""
        for style_id in style_ids:
            for style in self.chain(style_id):
                found = style.find(f"{qn(container)}/{qn(path)}")
                if found is not None:
                    return found
        return None


@dataclass
class ListLevel:
    fmt: str
    text: str
    start: int


class Numbering:
    def __init__(self, document: docx.document.Document) -> None:
        self.levels: dict[tuple[str, int], ListLevel] = {}
        self.counters: dict[str, list[int]] = {}
        try:
            root = document.part.numbering_part.element
        except (KeyError, NotImplementedError):
            return
        abstract = {a.get(qn("w:abstractNumId")): a for a in root.findall(qn("w:abstractNum"))}
        for num in root.findall(qn("w:num")):
            source = abstract.get(attr(num.find(qn("w:abstractNumId")), "val"))
            if source is None:
                continue
            for lvl in source.findall(qn("w:lvl")):
                self.levels[(num.get(qn("w:numId")), int(lvl.get(qn("w:ilvl"))))] = ListLevel(
                    attr(lvl.find(qn("w:numFmt")), "val", "decimal"), attr(lvl.find(qn("w:lvlText")), "val", ""),
                    int(attr(lvl.find(qn("w:start")), "val", "1")))

    def level(self, num_id: str, level: int) -> ListLevel | None:
        return self.levels.get((num_id, level))

    def label(self, num_id: str, level: int) -> str:
        """Advance the list and return its printed marker, eg 'a)' or '2.'."""
        counts = self.counters.setdefault(num_id, [])
        del counts[level + 1:]
        while len(counts) <= level:
            depth = len(counts)
            definition = self.level(num_id, depth)
            counts.append((definition.start if definition else 1) - 1)
        counts[level] += 1
        definition = self.level(num_id, level)
        text = definition.text if definition else "%1."

        def fill(match: re.Match) -> str:
            depth = int(match.group(1)) - 1
            inner = self.level(num_id, depth)
            return format_count(counts[depth] if depth < len(counts) else 1, inner.fmt if inner else "decimal")
        return re.sub(r"%(\d)", fill, text)


def format_count(value: int, fmt: str) -> str:
    if fmt in ("lowerLetter", "upperLetter"):
        letter = chr(ord("a") + (value - 1) % LETTER_COUNT)
        return letter.upper() if fmt == "upperLetter" else letter
    if fmt in ("lowerRoman", "upperRoman"):
        out = ""
        for size, numeral in ROMAN:
            while value >= size:
                out += numeral
                value -= size
        return out.upper() if fmt == "upperRoman" else out
    return str(value)


# ---------------------------------------------------------------- reader

@dataclass
class SectionInfo:
    landscape: bool
    text_height_pt: float


@dataclass
class Reader:
    document: docx.document.Document
    guidance: frozenset[str] = frozenset()
    notes: list[str] = field(default_factory=list)
    dropped: Counter = field(default_factory=Counter)
    dropped_previews: list[str] = field(default_factory=list)
    section: int = 1
    counters: list[int] = field(default_factory=list)
    stats: Counter = field(default_factory=Counter)
    unmapped_symbols: set[str] = field(default_factory=set)
    note_numbers: dict[_Element, str] = field(default_factory=dict)  # reference element -> printed number
    note_bodies: dict[tuple[str, str], _Element] = field(default_factory=dict)  # (kind, id) -> w:footnote/w:endnote
    pending_figures: list[Figure] = field(default_factory=list)  # images found inside table cells
    unconverted_images: list[tuple[str, bytes]] = field(default_factory=list)

    def read_note_parts(self, body: _Element) -> None:
        """Load footnotes.xml/endnotes.xml and number each reference in reading order, as Word prints them."""
        for rel in self.document.part.rels.values():
            for kind in NOTE_KINDS:
                if rel.reltype.endswith(f"/{kind}s"):
                    root = parse_xml(rel.target_part.blob)
                    self.hidden_text |= field_code_text(root)
                    for note in root:
                        if attr(note, "type") not in NOTE_SEPARATOR_TYPES:
                            self.note_bodies[(kind, note.get(qn("w:id")))] = note
        counts: Counter = Counter()
        for reference in body.iter(qn("w:footnoteReference"), qn("w:endnoteReference")):
            if in_deleted_run(reference):
                continue
            kind = local(reference).removesuffix("Reference")
            counts[kind] += 1
            self.note_numbers[reference] = format_count(counts[kind], NOTE_NUMBER_FORMATS[kind])
            self.stats[f"{kind}s"] += 1

    def __post_init__(self) -> None:
        body = self.document.element.body
        self.stats["inserted"] = len(list(body.iter(qn("w:ins"))))
        self.stats["deleted"] = len(list(body.iter(qn("w:del"))))
        self.stats["moved"] = len(list(body.iter(qn("w:moveTo"))))
        self.stats["equations"] = len(list(body.iter(f"{{{MATH_NS}}}oMath")))
        self.merge_deleted_paragraph_marks(body)
        self.hidden_text = field_code_text(body)
        self.read_note_parts(body)
        self.styles = Styles(self.document)
        self.numbering = Numbering(self.document)
        self.sections = self.read_sections()

    def merge_deleted_paragraph_marks(self, root: _Element) -> None:
        """Apply tracked paragraph-mark deletions the way Word does: the paragraph joins the next one.

        The joined paragraph keeps the first paragraph's style. A paragraph that is deleted
        entirely (no visible content left) disappears and the next one keeps its own style.
        """
        for paragraph in list(root.iter(qn("w:p"))):
            while paragraph.getparent() is not None and mark_deleted(paragraph):
                following = paragraph.getnext()
                if following is None or following.tag != qn("w:p"):
                    break
                if not has_visible_content(paragraph) and paragraph.find(f"{qn('w:pPr')}/{qn('w:sectPr')}") is None:
                    paragraph.getparent().remove(paragraph)
                    self.stats["paragraphs removed"] += 1
                    break
                for child in [c for c in following if c.tag != qn("w:pPr")]:
                    paragraph.append(child)
                following_props = following.find(qn("w:pPr"))
                section_end = None if following_props is None else following_props.find(qn("w:sectPr"))
                if section_end is not None:
                    paragraph.get_or_add_pPr().append(section_end)
                if not mark_deleted(following):
                    paragraph.find(f"{qn('w:pPr')}/{qn('w:rPr')}").remove(deleted_mark(paragraph))
                paragraph.getparent().remove(following)
                self.stats["paragraphs merged"] += 1

    # -- sections

    def read_sections(self) -> list[SectionInfo]:
        body = self.document.element.body
        properties = [p.find(f"{qn('w:pPr')}/{qn('w:sectPr')}") for p in body.findall(qn("w:p"))]
        properties = [sect for sect in properties if sect is not None]
        last = body.find(qn("w:sectPr"))
        if last is not None:
            properties.append(last)
        return [section_info(sect) for sect in properties] or [section_info(None)]

    def current_section(self) -> SectionInfo:
        return self.sections[min(self.section, len(self.sections)) - 1]

    # -- text

    def runs(self, node: _Element) -> Iterator[_Element]:
        """Runs in reading order with tracked insertions accepted and deletions dropped."""
        for child in node:
            name = local(child)
            if name == "r":
                yield child
            elif name in ("ins", "hyperlink", "smartTag", "fldSimple", "customXml", "moveTo", "sdt", "sdtContent"):
                yield from self.runs(child)
            elif child.tag.startswith(f"{{{MATH_NS}}}") and name not in MATH_PROPERTY_ELEMENTS:
                yield from self.runs(child)  # equations are read as their text, in order

    def run_text(self, run: _Element) -> str:
        parts = []
        for child in run:
            name = local(child)
            if name == "t":
                if child not in self.hidden_text:
                    parts.append(child.text or "")
            elif name in ("tab", "br", "cr"):
                parts.append(" ")
            elif name == "noBreakHyphen":
                parts.append("-")
            elif name == "sym":
                char = symbol_char(child)
                if char is None:
                    label = f"{attr(child, 'font')} U+{attr(child, 'char')}"
                    self.unmapped_symbols.add(label)
                    char = f"[symbol {label}]"
                parts.append(char)
        return "".join(parts)

    def note_reference(self, run: _Element) -> _Element | None:
        for child in run:
            if local(child) in ("footnoteReference", "endnoteReference"):
                return child
        return None

    def run_format(self, run: _Element, style_ids: list[str | None]) -> tuple[bool, bool, bool, float]:
        run_style = attr(run.find(f"{qn('w:rPr')}/{qn('w:rStyle')}"), "val")
        ids = [run_style, *style_ids]

        def toggle(tag: str) -> bool:
            direct = flag(run.find(f"{qn('w:rPr')}/{qn(tag)}"))
            if direct is not None:
                return direct
            styled = flag(self.styles.lookup(ids, "w:rPr", tag))
            if styled is not None:
                return styled
            default = None if self.styles.default_rpr is None else self.styles.default_rpr.find(qn(tag))
            return bool(flag(default))

        size_element = run.find(f"{qn('w:rPr')}/{qn('w:sz')}")
        if size_element is None:
            size_element = self.styles.lookup(ids, "w:rPr", "w:sz")
        if size_element is None and self.styles.default_rpr is not None:
            size_element = self.styles.default_rpr.find(qn("w:sz"))
        size = int(attr(size_element, "val", "0")) / HALF_POINTS_PER_PT or DEFAULT_FONT_SIZE
        vertical = run.find(f"{qn('w:rPr')}/{qn('w:vertAlign')}")
        if vertical is None:
            vertical = self.styles.lookup(ids, "w:rPr", "w:vertAlign")
        return toggle("w:b"), toggle("w:i"), attr(vertical, "val") == "superscript", size

    def spans(self, paragraph: _Element, style_ids: list[str | None]) -> list[Span]:
        out: list[Span] = []
        def add(text: str, bold: bool, italic: bool, superscript: bool, size: float) -> None:
            if out and (out[-1].bold, out[-1].italic, out[-1].superscript, out[-1].size) == (bold, italic, superscript, size):
                out[-1].text += text
            else:
                out.append(Span(text, bold, italic, superscript, size))

        for run in self.runs(paragraph):
            bold, italic, superscript, size = self.run_format(run, style_ids)
            reference = self.note_reference(run)
            if reference is not None and reference in self.note_numbers:
                add(self.note_numbers[reference], bold, italic, True, size)
            text = self.run_text(run)
            if text:
                add(text, bold, italic, superscript or in_math_superscript(run), size)
        return tidy_spans(out)

    # -- paragraphs

    def paragraph_style(self, paragraph: _Element) -> tuple[str | None, str]:
        style_id = attr(paragraph.find(f"{qn('w:pPr')}/{qn('w:pStyle')}"), "val")
        return style_id, self.styles.name(style_id)

    def list_marker(self, paragraph: _Element, style_id: str | None) -> tuple[str, int] | None:
        """(numId, level) of the list the paragraph belongs to, from itself or its style."""
        num_pr = paragraph.find(f"{qn('w:pPr')}/{qn('w:numPr')}")
        if num_pr is None:
            num_pr = self.styles.lookup([style_id], "w:pPr", "w:numPr")
        if num_pr is None:
            return None
        num_id = attr(num_pr.find(qn("w:numId")), "val")
        level = int(attr(num_pr.find(qn("w:ilvl")), "val", "0"))
        return (num_id, level) if num_id and num_id != "0" else None

    def paragraph(self, element: _Element, in_cell: bool = False) -> Paragraph | None:
        """A body paragraph, or None when it is empty or template guidance."""
        style_id, style_name = self.paragraph_style(element)
        spans = self.spans(element, [style_id])
        if not spans:
            return None
        text = "".join(s.text for s in spans).strip()
        if GUIDANCE_MARKER.match(text):
            self.dropped["start/end markers"] += 1
            return None
        if PLACEHOLDER_ONLY.match(text) or not any(ch.isalnum() for ch in text):
            self.dropped["placeholder-only paragraphs"] += 1
            return None
        # Table cells and one-or-two-word labels ("Description", "Population:") match the template by
        # coincidence: they are the sponsor's own headers and labels, so they are never dropped.
        droppable = not in_cell and len(text.split()) >= MIN_GUIDANCE_WORDS
        if droppable and style_name in GUIDANCE_STYLES and is_template_guidance(text, self.guidance):
            self.dropped[f"template guidance paragraphs ({style_name})"] += 1
            self.dropped_previews.append(text[:DROPPED_PREVIEW_LENGTH])
            return None
        kind, level = "body", 0
        marker = self.list_marker(element, style_id)
        definition = self.numbering.level(*marker) if marker else None
        if (definition and definition.fmt == "bullet") or (not marker and BULLET_STYLE.search(style_name)):
            kind, level = "bullet", marker[1] if marker else int("indent" in style_name)
        elif marker and not NUMBERED_HEADING.match(style_name):
            spans.insert(0, Span(self.numbering.label(*marker) + " ", False, False, False, spans[0].size))
        elif "caption" in style_name or is_caption(text):
            kind = "caption"
        return Paragraph(spans, kind, level, self.section)

    def figures(self, element: _Element) -> list[Figure | Paragraph]:
        out: list[Figure | Paragraph] = []
        for run in self.runs(element):
            for rel_id, width in image_references(run):
                part = self.document.part.related_parts.get(rel_id)
                png = to_png(part.blob) if part is not None else None
                if png is None:
                    kind = part.content_type if part is not None else "missing"
                    if part is not None:
                        self.unconverted_images.append((Path(str(part.partname)).name, part.blob))
                    self.notes.append(f"section {self.section}: image ({kind}) could not be converted; placeholder kept "
                                      "and original bytes saved on Document.unconverted_images")
                    out.append(Paragraph([Span("[Figure not converted]", False, True, False, DEFAULT_FONT_SIZE)],
                                         "body", 0, self.section))
                else:
                    out.append(Figure(png, width, self.section))
        return out

    # -- tables

    def table(self, element: _Element) -> Table | None:
        grid_cols = [twips_to_pt(attr(col, "w")) for col in element.findall(f"{qn('w:tblGrid')}/{qn('w:gridCol')}")]
        rows: list[list[dict | None]] = []
        origins: dict[int, dict] = {}  # grid column -> cell that a vMerge continues
        for tr in element.findall(qn("w:tr")):
            row: list[dict | None] = []
            before = int(attr(tr.find(f"{qn('w:trPr')}/{qn('w:gridBefore')}"), "val", "0"))
            row.extend(empty_cell() for _ in range(before))
            for tc in tr.findall(qn("w:tc")):
                props = tc.find(qn("w:tcPr"))
                span = int(attr(props.find(qn("w:gridSpan")), "val", "1")) if props is not None else 1
                merge = props.find(qn("w:vMerge")) if props is not None else None
                column = len(row)
                if merge is not None and attr(merge, "val", "continue") == "continue" and column in origins:
                    origins[column]["rowspan"] += 1
                    row.extend([None] * span)
                    continue
                cell = {"rowspan": 1, "colspan": span, "paragraphs": self.cell_paragraphs(tc), "fill": cell_fill(props)}
                if merge is not None:
                    origins[column] = cell
                else:
                    origins.pop(column, None)
                row.append(cell)
                row.extend([None] * (span - 1))
            rows.append(row)
        if not rows:
            return None
        width = max(len(grid_cols), *(len(row) for row in rows))
        for row in rows:
            row.extend(empty_cell() for _ in range(width - len(row)))
        if len(grid_cols) < width:
            grid_cols += [sum(grid_cols) / max(len(grid_cols), 1) if grid_cols else TITLE_LABEL_COLUMN_PT] * (width - len(grid_cols))
        padding = table_padding(element)
        height = estimate_height(rows, grid_cols, padding)
        section = self.current_section()
        first_page = self.section if height <= section.text_height_pt else self.section - 1
        return Table(rows, grid_cols, self.section, first_page, padding, height)

    def cell_paragraphs(self, tc: _Element) -> list[Paragraph]:
        out: list[Paragraph] = []
        for child in tc:
            if local(child) == "p":
                for figure in self.figures(child):
                    if isinstance(figure, Figure):
                        # A cell cannot hold an image downstream: leave a marker and put the image after the table.
                        self.pending_figures.append(figure)
                        self.stats["cell images"] += 1
                        figure = Paragraph([Span(FIGURE_MARKER, False, True, False, DEFAULT_FONT_SIZE)], "body", 0, self.section)
                    out.append(figure)
                paragraph = self.paragraph(child, in_cell=True)
                if paragraph:
                    out.append(paragraph)
                out.extend(b for b in self.textbox_blocks(child) if isinstance(b, Paragraph))
            elif local(child) == "tbl":
                nested = self.table(child)
                if nested:
                    self.notes.append(f"section {self.section}: nested table flattened into its cell")
                    out.extend(p for row in nested.grid for cell in row if cell for p in cell["paragraphs"])
        return out

    # -- text boxes and footnotes

    def textbox_blocks(self, element: _Element) -> list[Paragraph | Table]:
        """Text-box content anchored in `element`, to be placed right after it.

        A text box is stored twice when Word wrote an mc:AlternateContent (DrawingML choice and a VML
        fallback); only the choice is read.
        """
        out: list[Paragraph | Table] = []
        for box in element.iter(qn("w:txbxContent")):
            ancestors = list(box.iterancestors())
            if any(a.tag == qn("w:txbxContent") for a in ancestors):
                continue  # read through its outer box
            fallback = next((a for a in ancestors if local(a) == "Fallback"), None)
            if fallback is not None and any(local(c) == "Choice" for c in fallback.getparent()):
                continue
            self.stats["text boxes"] += 1
            for child in box:
                if local(child) == "p":
                    paragraph = self.paragraph(child)
                    if paragraph:
                        out.append(paragraph)
                    out.extend(self.textbox_blocks(child))
                elif local(child) == "tbl":
                    table = self.table(child)
                    if table:
                        out.append(table)
        return out

    def note_blocks(self, element: _Element) -> list[Paragraph]:
        """Footnote and endnote text for the references inside `element`, numbered like their references."""
        out: list[Paragraph] = []
        for reference in element.iter(qn("w:footnoteReference"), qn("w:endnoteReference")):
            note = self.note_bodies.get((local(reference).removesuffix("Reference"), reference.get(qn("w:id"))))
            if note is None or reference not in self.note_numbers:
                continue
            first = True
            for source in note.findall(qn("w:p")):
                paragraph = self.paragraph(source)
                if paragraph is None:
                    continue
                if first:
                    size = paragraph.spans[0].size
                    paragraph.spans[:0] = [Span(self.note_numbers[reference], False, False, True, size),
                                           Span(" ", False, False, False, size)]
                    first = False
                out.append(paragraph)
        return out

    def side_blocks(self, element: _Element) -> list:
        return [*self.textbox_blocks(element), *self.note_blocks(element)]

    # -- body walk

    def body_elements(self, parent: _Element) -> Iterator[_Element]:
        for child in parent:
            name = local(child)
            if name in ("p", "tbl"):
                yield child
            elif name == "sdt":
                gallery = child.find(f"{qn('w:sdtPr')}/{qn('w:docPartObj')}/{qn('w:docPartGallery')}")
                if attr(gallery, "val", "").casefold() == "table of contents":
                    continue
                content = child.find(qn("w:sdtContent"))
                if content is not None:
                    yield from self.body_elements(content)

    def read(self) -> Document:
        doc = Document()
        region = "title"
        title_items: list[Paragraph | Table] = []
        landscape_seen: bool | None = None

        def sink() -> list | None:
            return {"title": title_items, "front": doc.front, "body": doc.body, "skip": None}[region]

        def finish_title() -> None:
            doc.title_tables.extend(title_page_tables(title_items, self.notes))
            title_items.clear()

        def start_front(title: str) -> None:
            nonlocal region
            if region == "title":
                finish_title()
            region = "skip" if SKIPPED_FRONT_MATTER.match(title) else "front"
            if region == "front":
                doc.front.append(FrontHeading(title.title() if title.isupper() else title, self.section))

        for element in self.body_elements(self.document.element.body):
            has_section_end = element.find(f"{qn('w:pPr')}/{qn('w:sectPr')}") is not None if local(element) == "p" else False
            if local(element) == "tbl":
                table = self.table(element)
                target = sink()
                if table and target is not None:
                    target.append(table)
                    if region == "title":
                        self.stats["title images"] += len(self.pending_figures)
                    else:
                        target.extend(self.pending_figures)
                    target.extend(self.note_blocks(element))
                self.pending_figures.clear()
                continue
            style_id, style_name = self.paragraph_style(element)
            text = "".join(s.text for s in self.spans(element, [style_id])).strip()
            numbered = NUMBERED_HEADING.match(style_name)
            handled = True
            if numbered and text:
                level = int(numbered.group(1))
                if region != "body" and level != 1:
                    start_front(text)
                else:
                    if region != "body":
                        if region == "title":
                            finish_title()
                        region = "body"
                    self.add_heading(doc, level, text)
                    landscape_seen = self.mark_page(doc, landscape_seen, force=landscape_seen is None)
            elif text and region != "body" and (FRONT_HEADING_STYLE.match(style_name) or style_name == PAGE_TITLE_STYLE
                                                or self.looks_like_front_heading(element, text)):
                if region == "title" and COVER_HEADING.match(text):
                    pass
                else:
                    start_front(text)
            elif text and region == "body" and FRONT_HEADING_STYLE.match(style_name):
                doc.body.append(Subheading(text, "bold", self.section))
            else:
                handled = False
            if not handled:
                if region == "skip" and text and not TOC_STYLE.match(style_name) and not TOC_LEADER.search(text):
                    region = "front"  # the contents list ended without a new heading
                self.add_content(element, region, style_name, text, sink())
            if sink() is not None:
                sink().extend(self.side_blocks(element))
            if has_section_end:
                self.section += 1
                if region == "body":
                    landscape_seen = self.mark_page(doc, landscape_seen)
        if region == "title":
            finish_title()
        return doc

    def looks_like_front_heading(self, element: _Element, text: str) -> bool:
        spans = self.spans(element, [self.paragraph_style(element)[0]])
        return bool(FRONT_MATTER_HEADINGS.match(text)) and all(s.bold for s in spans if s.text.strip())

    def add_heading(self, doc: Document, level: int, text: str) -> None:
        for missing_level in range(len(self.counters) + 1, level):
            # Same as the PDF path: a skipped level becomes a placeholder heading so numbers stay valid.
            missing = advance(self.counters, missing_level)
            doc.body.append(Heading(missing, MISSING_HEADING_TITLE, self.section))
            self.notes.append(f"heading {missing}: level skipped before '{text}'; placeholder inserted")
        number = advance(self.counters, level)
        match = TYPED_NUMBER.match(text)
        if match and ("." in match.group(1) or match.group(1) == number):
            if match.group(1) != number:
                self.notes.append(f"heading '{text}': typed number {match.group(1)} differs from counted {number}")
            text = text[match.end():]
        doc.body.append(Heading(number, text.strip(), self.section))

    def mark_page(self, doc: Document, last: bool | None, force: bool = False) -> bool:
        landscape = self.current_section().landscape
        if force or landscape != last:
            doc.body.append(PageMark(landscape, self.section))
        return landscape

    def add_content(self, element: _Element, region: str, style_name: str, text: str,
                    target: list | None) -> None:
        if target is None:
            return
        for figure in self.figures(element):
            if region == "title":
                self.stats["title images"] += 1  # the PDF path keeps no title-page images either
            else:
                target.append(figure)
        if style_name == LABEL_STYLE and region == "body" and text and len(text) <= LABEL_MAX_LENGTH \
                and ":" not in text and not text.endswith((".", ";", ",")):
            target.append(Subheading(text, "label", self.section))
            return
        paragraph = self.paragraph(element)
        if paragraph:
            target.append(paragraph)


LABEL_MAX_LENGTH = 80


# ---------------------------------------------------------------- helpers

def is_caption(text: str) -> bool:
    """'Table 3 Title' / 'Figure 1. Title': one short sentence after the label (the label's own period is allowed)."""
    match = CAPTION.match(text)
    return bool(match) and len(text) <= MAX_CAPTION_LENGTH and ". " not in text[match.end(0) - 1:-1]


def deleted_mark(paragraph: _Element) -> _Element | None:
    """The tracked-deletion marker on a paragraph mark, if any."""
    return paragraph.find(f"{qn('w:pPr')}/{qn('w:rPr')}/{qn('w:del')}")


def mark_deleted(paragraph: _Element) -> bool:
    return deleted_mark(paragraph) is not None


def in_deleted_run(node: _Element) -> bool:
    return any(a.tag in (qn("w:del"), qn("w:moveFrom")) for a in node.iterancestors())


def has_visible_content(paragraph: _Element) -> bool:
    for node in paragraph.iter(qn("w:t"), qn("w:drawing"), qn("w:pict")):
        if node.tag != qn("w:t") or ((node.text or "").strip() and not in_deleted_run(node)):
            return True
    return False


def field_code_text(root: _Element) -> set[_Element]:
    """Text elements that sit between a field's begin and separate marks, ie the code and not its result.

    Fields nest (an EndNote citation inside a hyperlink) and can span paragraphs, so this walks the
    whole tree once in document order with a stack.
    """
    hidden: set[_Element] = set()
    stack: list[str] = []
    for node in root.iter(qn("w:fldChar"), qn("w:t")):
        if in_deleted_run(node):
            continue
        if node.tag == qn("w:t"):
            if "code" in stack:
                hidden.add(node)
            continue
        kind = attr(node, "fldCharType")
        if kind == "begin":
            stack.append("code")
        elif kind == "separate" and stack:
            stack[-1] = "result"
        elif kind == "end" and stack:
            stack.pop()
    return hidden


def symbol_char(element: _Element) -> str | None:
    """The character a w:sym stands for, or None when its font/code is not in the tables."""
    font = (attr(element, "font", "") or "").casefold()
    code = int(attr(element, "char", "0") or "0", 16)
    code &= 0xFF  # symbol fonts keep their glyphs at 0xF0xx in the private-use area
    if font == "symbol":
        return SYMBOL_FONT_CHARS.get(code) or GREEK_LETTERS.get(chr(code))
    if font.startswith("wingdings"):
        return WINGDINGS_CHARS.get(code)
    return None


GREEK_LOWER = dict(zip("abcdefghijklmnopqrstuvwxyz", "αβχδεφγηιϕκλμνοπθρστυϖωξψζ"))
GREEK_UPPER = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ"))
GREEK_LETTERS = {**GREEK_LOWER, **GREEK_UPPER}
SYMBOL_FONT_CHARS = {
    0xA3: "≤", 0xB3: "≥", 0xB1: "±", 0xB0: "°", 0xB4: "×", 0xB8: "÷", 0xB9: "≠", 0xBB: "≈", 0xA5: "∞",
    0xB5: "µ", 0xAE: "→", 0xAC: "←", 0xAD: "↑", 0xAF: "↓", 0xDE: "⇒", 0xDB: "⇔", 0xD6: "√", 0xB7: "•",
    0xA2: "′", 0xB2: "″", 0xB6: "∂", 0xD1: "∇", 0xC5: "∅", 0xCE: "∈", 0xD0: "∠", 0xE5: "∑", 0xF2: "∫",
    0x2D: "−", 0x2B: "+", 0x3D: "=", 0x3C: "<", 0x3E: ">", 0x20: " ",
}
WINGDINGS_CHARS = {0xFC: "✓", 0xFB: "✗", 0xA7: "▪", 0x6E: "■", 0x71: "❑", 0xD8: "➢", 0x76: "❖", 0xA8: "◻", 0xE0: "→"}


def in_math_superscript(run: _Element) -> bool:
    return any(a.tag == f"{{{MATH_NS}}}sup" for a in run.iterancestors())


def section_info(sect: _Element | None) -> SectionInfo:
    size = None if sect is None else sect.find(qn("w:pgSz"))
    margin = None if sect is None else sect.find(qn("w:pgMar"))
    width = int(attr(size, "w", str(DEFAULT_PAGE_SIZE_TWIPS[0])))
    height = int(attr(size, "h", str(DEFAULT_PAGE_SIZE_TWIPS[1])))
    landscape = attr(size, "orient") == "landscape" or width > height
    vertical = int(attr(margin, "top", str(DEFAULT_MARGIN_TWIPS))) + int(attr(margin, "bottom", str(DEFAULT_MARGIN_TWIPS)))
    return SectionInfo(landscape, (height - vertical) / TWIPS_PER_PT)


def empty_cell() -> dict:
    return {"rowspan": 1, "colspan": 1, "paragraphs": [], "fill": None}


def cell_fill(props: _Element | None) -> str | None:
    shade = None if props is None else props.find(qn("w:shd"))
    fill = attr(shade, "fill", "auto")
    return None if fill.lower() in ("auto", "ffffff") else fill.upper()


def table_padding(table: _Element) -> tuple[float, float]:
    margins = table.find(f"{qn('w:tblPr')}/{qn('w:tblCellMar')}")
    if margins is None:
        return DEFAULT_CELL_PADDING
    horizontal = twips_to_pt(attr(margins.find(qn("w:left")), "w")) or DEFAULT_CELL_PADDING[0]
    vertical = twips_to_pt(attr(margins.find(qn("w:top")), "w")) or DEFAULT_CELL_PADDING[1]
    return horizontal, vertical


def estimate_height(rows: list, widths: list[float], padding: tuple[float, float]) -> float:
    total = 0.0
    for row in rows:
        row_height = 0.0
        for column, cell in enumerate(row):
            if not cell:
                continue
            width = max(sum(widths[column:column + cell["colspan"]]) - 2 * padding[0], 1.0)
            lines = 0
            for paragraph in cell["paragraphs"]:
                size = max((s.size for s in paragraph.spans), default=DEFAULT_TABLE_FONT_SIZE)
                lines += max(1, math.ceil(len(paragraph.text) * size * AVERAGE_CHAR_WIDTH_FACTOR / width))
            row_height = max(row_height, max(lines, 1) * DEFAULT_TABLE_FONT_SIZE * LINE_HEIGHT_FACTOR)
        total += row_height + 2 * padding[1]
    return total


def image_references(run: _Element) -> Iterator[tuple[str, float]]:
    """(relationship id, width in pt) for each picture in a run, skipping mc:Fallback duplicates."""
    def in_fallback(node: _Element) -> bool:
        return any(local(a) == "Fallback" for a in node.iterancestors())

    found = False
    for blip in run.iter(f"{{{A_NS}}}blip"):
        rel_id = blip.get(f"{{{R_NS}}}embed")
        if not rel_id or in_fallback(blip):
            continue
        found = True
        extent = None
        for ancestor in blip.iterancestors():
            if local(ancestor) in ("inline", "anchor"):
                extent = ancestor.find(f"{{{WP_NS}}}extent")
                break
        yield rel_id, int(extent.get("cx", "0")) / EMU_PER_PT if extent is not None else 0.0
    if not found:
        for image in run.iter(f"{{{V_NS}}}imagedata"):
            rel_id = image.get(f"{{{R_NS}}}id")
            if rel_id and not in_fallback(image):
                yield rel_id, 0.0


A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
V_NS = "urn:schemas-microsoft-com:vml"


def to_png(blob: bytes) -> bytes | None:
    if blob.startswith(PNG_SIGNATURE):
        return blob
    try:
        pixmap = pymupdf.Pixmap(blob)
        if pixmap.alpha or pixmap.colorspace is None or pixmap.colorspace.n != 3:
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
        return pixmap.tobytes("png")
    except Exception:  # MuPDF raises its own error types for EMF/WMF and other formats it cannot open
        return None


# ---------------------------------------------------------------- title page

def cell_of(text: str, bold: bool = False) -> dict:
    cell = empty_cell()
    if text:
        cell["paragraphs"] = [Paragraph([Span(text, bold, False, False, DEFAULT_FONT_SIZE)])]
    return cell


def cell_text(cell: dict | None) -> str:
    return " ".join(p.text for p in cell["paragraphs"]).strip() if cell else ""


def title_page_tables(items: list[Paragraph | Table], notes: list[str]) -> list[Table]:
    """Label/value pairs from the title page as one synthetic two-column table (plus real 2-column tables).

    Registry-id tables are kept as they are; any other table (eg the signature block) contributes
    its first filled cell to the field it follows.
    """
    fields: list[list[str]] = []  # [label, value]
    open_label = False
    extra: list[Table] = []
    for item in items:
        if isinstance(item, Table):
            rows = [[cell_text(c) for c in row if c] for row in item.grid]
            rows = [row for row in rows if any(row)]
            if rows and all(len(row) == 2 and row[0].endswith(":") for row in rows):
                extra.append(item)
            elif rows and fields:
                first = next((text for row in rows for text in row if text and text.casefold() != "date"), "")
                fields[-1][1] = f"{fields[-1][1]} {first}".strip()
            open_label = False
            continue
        for spans in split_at_labels(item.spans):
            text = "".join(s.text for s in spans).strip()
            label, value = split_label(spans)
            if label:
                fields.append([label, value])
                open_label = True
            elif not text:
                continue
            elif any(word in text.casefold() for word in CONTINUATION_BLOCKERS):
                fields.append([MEDICAL_MONITOR_LABEL, text])
                open_label = False
            elif DATE_ONLY.match(text):
                notes.append(f"title page: unlabeled date '{text}' not used")
            elif open_label and fields:
                fields[-1][1] = f"{fields[-1][1]} {text}".strip()
            else:
                fields.append(["", text])
    rows = [[cell_of(label, True), cell_of(value)] for label, value in fields if label or value]
    if not rows:
        return extra
    widths = [TITLE_LABEL_COLUMN_PT, TITLE_VALUE_COLUMN_PT]
    return [Table(rows, widths, 1, 1, DEFAULT_CELL_PADDING, 0.0), *extra]


# ---------------------------------------------------------------- entry points

def normalize(text: str) -> str:
    return " ".join(text.translate(DASHES).casefold().split())


def is_template_guidance(text: str, guidance: frozenset[str]) -> bool:
    candidate = normalize(text)
    if candidate in guidance:
        return True
    for known in guidance:
        matcher = difflib.SequenceMatcher(None, candidate, known, autojunk=False)
        if (matcher.real_quick_ratio() >= GUIDANCE_MATCH_RATIO and matcher.quick_ratio() >= GUIDANCE_MATCH_RATIO
                and matcher.ratio() >= GUIDANCE_MATCH_RATIO):
            return True
    return False


@lru_cache(maxsize=None)
def template_guidance(template: Path) -> frozenset[str]:
    """Normalized text of every CPT_Instructional paragraph (including table cells) in the template."""
    reader = Reader(docx.Document(str(template)))
    texts = set()
    for element in reader.document.element.body.iter(qn("w:p")):
        style_id, style_name = reader.paragraph_style(element)
        if style_name in GUIDANCE_STYLES:
            text = normalize("".join(s.text for s in reader.spans(element, [style_id])))
            if text:
                texts.add(text)
    return frozenset(texts)


def read_docx(path: Path, template: Path = DEFAULT_TEMPLATE) -> tuple[Document, list[str]]:
    """The Document plus notes about anything the reader could not carry over faithfully.

    `template` is the v010 CPT template whose guidance text is dropped from the protocol.
    """
    reader = Reader(docx.Document(str(path)), template_guidance(template))
    document = reader.read()
    for what, count in reader.dropped.items():
        reader.notes.append(f"dropped {count} {what}")
    for preview in reader.dropped_previews:
        reader.notes.append(f"dropped template guidance: {preview!r}")
    reader.notes.append(
        f"tracked changes: {reader.stats['inserted']} insertions kept, {reader.stats['deleted']} deletions dropped, "
        f"{reader.stats['moved']} moved passages kept at their new position")
    if reader.stats["paragraphs merged"] or reader.stats["paragraphs removed"]:
        reader.notes.append(
            f"tracked paragraph-mark deletions: {reader.stats['paragraphs merged']} paragraphs joined to the next one, "
            f"{reader.stats['paragraphs removed']} fully deleted paragraphs removed")
    for label, count in (("footnotes", reader.stats["footnotes"]), ("endnotes", reader.stats["endnotes"])):
        if count:
            reader.notes.append(f"{count} {label} read; their text follows the block that holds the reference")
    if reader.stats["text boxes"]:
        reader.notes.append(f"{reader.stats['text boxes']} text boxes read as paragraphs after their anchor paragraph")
    if reader.stats["cell images"]:
        reader.notes.append(f"{reader.stats['cell images']} images inside table cells: a '[Figure]' marker stays in the "
                            "cell and the image follows the table (cells cannot hold images downstream)")
    if reader.stats["title images"]:
        reader.notes.append(f"{reader.stats['title images']} title-page images not kept (the PDF path keeps none either)")
    if reader.stats["equations"]:
        reader.notes.append(f"{reader.stats['equations']} equations flattened to their text")
    if reader.unmapped_symbols:
        reader.notes.append("symbols without a known mapping kept as [symbol font U+code]: "
                            + ", ".join(sorted(reader.unmapped_symbols)))
    document.notes = reader.notes
    document.unconverted_images = reader.unconverted_images
    return document, reader.notes


def load_docx(path: Path, template: Path = DEFAULT_TEMPLATE) -> Document:
    """The Document; its `notes` attribute lists everything dropped or approximated (the CLI prints it in the report)."""
    return read_docx(path, template)[0]
