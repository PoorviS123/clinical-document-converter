"""Rebuild a protocol PDF as a .docx on a TransCelerate CPT template.

The output keeps the template's styles, numbering, page setup and section
layout, and fills the body with the PDF's own content: title page fields,
front matter, numbered headings mapped to the template's heading styles,
paragraphs, bullets, tables (with merged cells) and figures.

Usage:
    python pdf_to_v010_docx.py --template CPT_CoreBWE_v010.docx in.pdf out.docx

Needs PyMuPDF and python-docx.
"""

import argparse
import bisect
import copy
import io
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field

import docx
import pymupdf
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

BOLD_FLAG = 16
ITALIC_FLAG = 2
SUPERSCRIPT_FLAG = 1

PAGE_MARGIN_ZONE = 0.12  # top/bottom fraction of a page treated as running header/footer
BOILERPLATE_MIN_PAGE_SHARE = 0.25  # a margin line repeated on this share of pages is a header/footer
SAME_ROW_TOLERANCE = 3.0  # pt; lines whose tops differ by less sit on the same row
PARAGRAPH_GAP_FACTOR = 1.6  # a vertical gap this many line heights apart starts a new paragraph
BULLET_INDENT_STEP = 12.0  # pt of extra indent that makes a bullet one level deeper
SENTENCE_END = ".:;!?"
TOC_LEADER = re.compile(r"\.{5,}\s*\d*\s*$|\.{5,}")
TOC_PAGE_MIN_LEADER_SHARE = 0.25
MIN_FIGURE_SIDE = 40.0  # pt
MIN_VECTOR_FIGURE_AREA = 12000.0  # pt^2
REDACTION_FILL_SHARE = 0.8
REDACTION_MARK = "CCI"  # label sponsors print on redacted (commercially confidential) blocks
BLACK = (0.0, 0.0, 0.0)
WHITE = (1.0, 1.0, 1.0)
REDACTION_BAR_HEIGHT_RANGE = (4.0, 16.0)  # pt; a black box about one text line tall is an inline redaction
REDACTION_BAR_STROKE_SLACK = 1.0  # pt; a bar's outline sticks out past its rect
REDACTION_GLYPH = "\u2588"  # stands in for an inline redaction bar until it is written as a black run
REDACTION_BLOCK = -1  # block number given to the text line made for a redaction bar
NBSP = "\u00a0"
ZERO_WIDTH_SPACE = "\u200b"
NBSP_WIDTH_EM = 0.25  # width of a no-break space in Times, as a share of the font size
REDACTION_UNDERLINE_GAP = 3.0  # pt below a bar where the underline of the text it hides is drawn
DEFAULT_CELL_PADDING = (5.0, 2.0)  # pt (horizontal, vertical) when a table has no text to measure
MAX_CELL_PADDING = 10.0  # pt; a wider gap is centred or indented text, not padding
CELL_FILL_MIN_SHARE = 0.5  # a fill must cover this share of a cell to count as its shading
DEFAULT_TABLE_FONT_SIZE = 10.0  # pt, for a table with no measurable text
TABLE_PAGE_HEIGHT_SHARE = 0.9  # a one-page table may fill this much of the text height, leaving room for its caption
TWIPS_PER_PT = 20
TABLE_BULLET_HANG_EM = 1.5  # hanging indent of a bullet in a table cell, in ems of the table's font size
BORDER_EIGHTHS_OF_PT = 4  # border width in Word's units (1/8 pt): 0.5 pt, as in the template's Table Grid
RULE_MAX_THICKNESS = 2.0  # pt; a filled box thinner than this is a drawn line
RULE_POSITION_TOLERANCE = 2.0  # pt a rule may sit from a cell edge and still be drawn along it
RULE_MIN_LENGTH = 4.0  # pt; shorter marks are junction dots, not rules
BORDER_MIN_COVER = 0.5  # share of a cell side a rule must run along to count as its border
COLUMN_MIN_GAP = 12.0  # pt of blank space before text that starts a new column
COLUMN_START_MERGE = 20.0  # pt; column starts closer than this are one column (bullet and its hanging text)
MIN_COLUMN_LAYOUT_ROWS = 3  # rows with text in several columns before text is read as a borderless table
MAX_COLUMN_LAYOUT_GAP = 3.0  # line heights of blank space that end a borderless table
FULL_WIDTH_RULE_SHARE = 0.9  # a rule this share of a box's width splits the box into rows
MAX_SEPARATE_HEADER_ROWS = 2  # a table this short directly above another with the same columns is its header
TITLE_PAGES = 1  # leading pages read as title-page fields, never as tables
WRAPPED_LINE_MAX_GAP_SHARE = 0.3  # of a line height; lines closer than this are one wrapped entry
FIGURE_DPI = 150
TOC_DEPTH = 3
MAX_CAPTION_LENGTH = 220
MAX_LABEL_LENGTH = 80
MIN_TWO_COLUMN_LABELS = 3  # label rows needed before a title page is read as label | value columns

SECTION_NUMBER = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,5})\.?$")
COMBINED_HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,5})\.?\s+([A-Z].*)$")
MAX_SKIPPED_HEADINGS = 3  # sections a heading may skip (eg redacted ones) and still count as the next heading
MISSING_HEADING_TITLE = "[Heading not present in source PDF]"
WRAPPED_TITLE_MAX_INDENT = 60.0  # pt
HEADING_X_TOLERANCE = 30.0  # pt a heading may sit right of the first heading's left edge
APPENDIX_HEADING = re.compile(r"^Appendix\s+([A-Z])\b[\s:.]+\S")
LETTERED_SUBSECTION = re.compile(r"^([A-Z])\s?(\d{1,2}(?:\.\d{1,2}){0,3})$")
BULLET_GLYPHS = {"•", "", "", "", "", "o", "▪", "■", "–", "-", "○", "◦", "§", "", "•", "", "✓"}
LIST_MARKER = re.compile(r"\(?[0-9A-Za-z]{1,3}[.)]|\[\d{1,3}\]")
CAPTION = re.compile(r"^(Table|Figure)\s+[\dA-Z][\w.\-]*\s+\S")

FRONT_MATTER_HEADINGS = re.compile(
    r"^(document history|protocol amendment summary of changes( table)?|summary of changes|"
    r"overall rationale for the amendment|amended clinical trial protocol \d+|protocol history|"
    r"assessment of substantiality|overview of changes.*|amendment \d+ \(.*\)|amendment \d+|"
    r"list of abbreviations.*|abbreviations.*|definitions of terms|sponsor signatory|"
    r"signature page.*|investigator'?s? (agreement|signature|statement).*|protocol (amendment )?acceptance form|"
    r"confidentiality statement|protocol approval|table of contents|contents|list of tables|"
    r"list of figures|list of appendices)$",
    re.IGNORECASE,
)
SKIPPED_FRONT_MATTER = re.compile(r"^(table of contents|contents|list of tables|list of figures|list of appendices)$", re.IGNORECASE)

# Title-page labels seen in sponsor PDFs, mapped to a CPT field key.
TITLE_FIELD_PATTERNS = [
    ("title", r"(protocol|full|official|study|trial)?\s*title"),
    ("short_title", r"(brief|short)\s+title"),
    ("acronym", r"(trial |study )?acronym|(trial|study) name"),
    ("protocol_number", r"(clinical (trial|study) )?protocol (number|identifier|no\.?)|sponsor protocol identifier|study number|protocol identifier|trial id"),
    ("amendment_number", r"amendment (number|identifier|no\.?)"),
    ("version", r"(protocol )?version( number)?"),
    ("date", r"(version |approval |protocol |release )?date"),
    ("compound", r"compound( number| name)?|investigational product( code)?\(?s?\)?|substance name|test product|study (drug|intervention)"),
    ("phase", r"(study|trial) phase|phase"),
    ("sponsor", r"sponsor( name)?( and (legal )?(registered )?address)?"),
    ("sponsor_address", r"legal registered address|sponsor address"),
    ("manufacturer", r"manufacturer"),
    ("regulatory_ids", r"regulatory agency identifier number\(?s?\)?|regulatory or clinical trial identifier\(?s?\)?|registry identifiers?"),
    ("signatory", r"sponsor signatory"),
    ("medical_monitor", r"medical (monitor|expert).*"),
]
CPT_TITLE_LABELS = {
    "v010": {
        "title": "Protocol Title:", "short_title": "Brief Title:", "acronym": "Acronym:",
        "protocol_number": "Protocol Number:", "amendment_number": "Amendment Number:", "version": "Version:",
        "date": "Approval Date:", "compound": "Compound:", "phase": "Study Phase:", "sponsor": "Sponsor Name:",
        "sponsor_address": "Legal Registered Address:", "manufacturer": "Manufacturer:",
        "regulatory_ids": "Regulatory Agency Identifier Number(s):", "signatory": "Sponsor Signatory:",
        "medical_monitor": "Medical Monitor Name and Contact Information:",
    },
    "v011": {
        "title": "Full Title:", "short_title": "Short Title:", "acronym": "Trial Acronym:",
        "protocol_number": "Sponsor Protocol Identifier:", "amendment_number": "Amendment Identifier:",
        "version": "Version Number:", "date": "Version Date:", "compound": "Investigational Product Code(s):",
        "phase": "Trial Phase:", "sponsor": "Sponsor Name and Address:", "sponsor_address": "Sponsor Name and Address:",
        "manufacturer": "Device Manufacturer Name and Legal Address:",
        "regulatory_ids": "Regulatory or Clinical Trial Identifier(s):", "signatory": "Sponsor Signatory:",
        "medical_monitor": "Medical Expert Contact:",
    },
}

STYLE_NAMES = {
    "v010": {
        "headings": ["Heading 1", "Heading 2", "Heading 3", "Heading 4", "Heading 5", "Heading 6"],
        "front_heading": "Heading 2 (non-numbered)",
        "body": "Normal", "bullet": "CPT_List Bullet", "caption": "Caption",
        "table_text": "Table Text", "field_label": "Heading (No TOC)", "page_title": "Heading 1 (No TOC)",
    },
    "v011": {
        "headings": ["01Heading 1", "02Heading 2", "03Heading 3", "04Heading 4", "05Heading 5", "06Heading 6"],
        "front_heading": "08Subheading Bold",
        "body": "Normal", "bullet": "CPT_List Bullet", "caption": "Caption",
        "table_text": "Table Text", "field_label": "Heading (No TOC)", "page_title": "Heading 1 (No TOC)",
    },
}


# ---------------------------------------------------------------- PDF model

@dataclass
class Span:
    text: str
    bold: bool
    italic: bool
    superscript: bool
    size: float


@dataclass
class Row:
    """One visual text row: PDF lines that share a baseline, left to right."""
    page: int
    x0: float
    y0: float
    y1: float
    block: int
    pieces: list  # list of (x0, list[Span])

    @property
    def text(self):
        return " ".join("".join(s.text for s in spans).strip() for _, spans in self.pieces).strip()

    @property
    def height(self):
        return self.y1 - self.y0

    def spans(self, skip_pieces=0):
        out = []
        for i, (_, spans) in enumerate(self.pieces[skip_pieces:]):
            if i:
                out.append(Span(" ", False, False, False, 0))
            out.extend(spans)
        return out

    def first_piece_text(self):
        return "".join(s.text for s in self.pieces[0][1]).strip()

    def is_bold(self):
        chars = [s for _, spans in self.pieces for s in spans if s.text.strip()]
        return bool(chars) and all(s.bold for s in chars)

    def is_italic(self):
        chars = [s for _, spans in self.pieces for s in spans if s.text.strip()]
        return bool(chars) and all(s.italic for s in chars)

    def size(self):
        sizes = [s.size for _, spans in self.pieces for s in spans if s.text.strip()]
        return max(sizes) if sizes else 0


@dataclass
class Heading:
    number: str
    title: str
    page: int

    @property
    def level(self):
        return self.number.count(".") + 1


@dataclass
class Paragraph:
    spans: list
    kind: str = "body"  # body | bullet | caption | label
    level: int = 0
    page: int = 0
    first_x: float = 0

    @property
    def text(self):
        return "".join(s.text for s in self.spans).strip()


@dataclass
class Table:
    grid: list  # rows of cells; a cell is None (covered by a merge) or dict
    col_widths: list
    page: int  # last page the table is on
    first_page: int
    padding: tuple  # pt (horizontal, vertical) between cell edges and their text
    height: float  # pt on the source page
    header_rows: int = 1  # leading rows repeated at the top of each page
    font_width_ratio: float = 1.0  # source text width over the same text in Times; below 1 for narrow fonts


@dataclass
class DrawnTable:
    """Cells and bounds of a table found by this script, shaped like a PyMuPDF table for table_grid."""
    cells: list
    bbox: tuple


@dataclass
class Figure:
    png: bytes
    width_pt: float
    page: int


@dataclass
class Redaction:
    page: int


@dataclass
class FrontHeading:
    title: str
    page: int


@dataclass
class PageMark:
    landscape: bool
    page: int


@dataclass
class Document:
    title_rows: list = field(default_factory=list)
    title_tables: list = field(default_factory=list)
    front: list = field(default_factory=list)
    body: list = field(default_factory=list)


def spans_from_line(line):
    out = []
    for s in line["spans"]:
        if not s["text"]:
            continue
        flags = s["flags"]
        bold = bool(flags & BOLD_FLAG) or "bold" in s["font"].lower()
        italic = bool(flags & ITALIC_FLAG) or "italic" in s["font"].lower()
        out.append(Span(s["text"], bold, italic, bool(flags & SUPERSCRIPT_FLAG), round(s["size"], 1)))
    return out


def inside(bbox, rect, slack=1.5):
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    return rect[0] - slack <= cx <= rect[2] + slack and rect[1] - slack <= cy <= rect[3] + slack


def overlaps(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def normalize_boilerplate(text):
    return re.sub(r"\d+", "#", text.strip().lower())


def find_boilerplate(pdf):
    counts = Counter()
    for page in pdf:
        seen = set()
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                if not in_running_header_zone(line["bbox"], page):
                    continue
                key = normalize_boilerplate("".join(s["text"] for s in line["spans"]))
                if key:
                    seen.add(key)
        counts.update(seen)
    threshold = max(3, BOILERPLATE_MIN_PAGE_SHARE * pdf.page_count)
    return {key for key, n in counts.items() if n >= threshold}


def is_running_header(line, page, page_lines, boilerplate):
    """A header/footer line: in the page margin and repeated across pages.

    Numbers-only lines (page numbers, but also section numbers like '2.1') only count when
    nothing follows them on the same row, so a heading number next to its title is kept.
    """
    if not in_running_header_zone(line["bbox"], page):
        return False
    key = normalize_boilerplate("".join(s["text"] for s in line["spans"]))
    if key not in boilerplate:
        return False
    if re.search(r"[a-z]", key):
        return True
    x1, y0 = line["bbox"][2], line["bbox"][1]
    return not any(abs(other["bbox"][1] - y0) < SAME_ROW_TOLERANCE and other["bbox"][0] >= x1
                   for other, _ in page_lines)


def in_margin_zone(bbox, page):
    height = page.rect.height
    return bbox[1] < PAGE_MARGIN_ZONE * height or bbox[3] > (1 - PAGE_MARGIN_ZONE) * height


def in_running_header_zone(bbox, page):
    """Like in_margin_zone, but measured on the page's long side: a running header sits as far
    from the top edge on a landscape page as on a portrait one."""
    zone = PAGE_MARGIN_ZONE * max(page.rect.width, page.rect.height)
    return bbox[1] < zone or bbox[3] > page.rect.height - zone


def group_rows(lines, page_no):
    """Merge PDF lines sitting on the same baseline into rows, top to bottom."""
    lines = sorted(lines, key=lambda item: (round(item[0]["bbox"][1]), item[0]["bbox"][0]))
    rows = []
    for line, block_no in lines:
        bbox = line["bbox"]
        spans = spans_from_line(line)
        if not "".join(s.text for s in spans).strip():
            continue
        row = rows[-1] if rows else None
        if row and abs(bbox[1] - row.y0) < SAME_ROW_TOLERANCE:
            row.pieces.append((bbox[0], spans))
            row.y1 = max(row.y1, bbox[3])
        else:
            rows.append(Row(page_no, bbox[0], bbox[1], bbox[3], block_no, [(bbox[0], spans)]))
    for row in rows:
        row.pieces.sort(key=lambda piece: piece[0])
        row.x0 = row.pieces[0][0]
    return rows


def is_toc_page(rows):
    if not rows:
        return False
    leaders = sum(1 for row in rows if TOC_LEADER.search(row.text))
    return leaders / len(rows) >= TOC_PAGE_MIN_LEADER_SHARE


def is_redaction_bar(drawing):
    rect = drawing["rect"]
    return (drawing.get("fill") == BLACK
            and REDACTION_BAR_HEIGHT_RANGE[0] <= rect.height <= REDACTION_BAR_HEIGHT_RANGE[1]
            and rect.width >= RULE_MAX_THICKNESS)


def redaction_bars(drawings):
    return [d["rect"] for d in drawings if is_redaction_bar(d)]


def find_tables(page, drawings):
    """Tables on the page, read from ruled lines only.

    Cell fills and inline redaction bars are left out: their edges would otherwise split rows
    and columns that the source draws as one cell.
    """
    bars = redaction_bars(drawings)
    if not bars:
        return page.find_tables(strategy="lines_strict").tables
    scratch = pymupdf.open()
    scratch.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
    clean = scratch[0]
    slack = REDACTION_BAR_STROKE_SLACK
    for rect in bars:
        clean.add_redact_annot(rect + (-slack, -slack, slack, slack))
    clean.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                           graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                           text=pymupdf.PDF_REDACT_TEXT_NONE)
    return clean.find_tables(strategy="lines_strict").tables


def cell_fill(cell, drawings):
    """Hex colour of the shading behind a cell, or None for white/unshaded cells."""
    area = (cell[2] - cell[0]) * (cell[3] - cell[1])
    for d in drawings:
        fill = d.get("fill")
        r = d["rect"]
        if fill in (None, WHITE, BLACK) or not inside(tuple(cell), tuple(r)):
            continue
        if r.width * r.height >= CELL_FILL_MIN_SHARE * area:
            return "".join(f"{round(channel * 255):02X}" for channel in fill[:3])
    return None


def cell_padding(table, lines):
    """Median gap between cell edges and the text inside them, as (horizontal, vertical)."""
    gaps_x, gaps_y = [], []
    for cell in table.cells:
        if not cell:
            continue
        inner = [ln[0]["bbox"] for ln in lines if inside(ln[0]["bbox"], cell)]
        if inner:
            gaps_x.append(min(b[0] for b in inner) - cell[0])
            gaps_y.append(min(b[1] for b in inner) - cell[1])

    def typical(gaps, default):
        plausible = [g for g in gaps if 0 <= g <= MAX_CELL_PADDING]
        return statistics.median(plausible) if plausible else default

    return typical(gaps_x, DEFAULT_CELL_PADDING[0]), typical(gaps_y, DEFAULT_CELL_PADDING[1])


def without_redaction_marks(drawings):
    """Drawings other than inline redaction bars and the underlines drawn with them."""
    slack = REDACTION_BAR_STROKE_SLACK + REDACTION_UNDERLINE_GAP
    zones = [bar + (-slack, -slack, slack, slack) for bar in redaction_bars(drawings)]
    return [d for d in drawings
            if not is_redaction_bar(d) and not any(zone.contains(d["rect"]) for zone in zones)]


def redaction_bar_lines(drawings, lines):
    """Each inline redaction bar as a text line holding redaction glyphs as wide as the bar, so
    it stays in its place in the sentence (or cell) instead of being dropped."""
    out = []
    for bar in redaction_bars(drawings):
        def overlap(line):
            return min(bar.y1, line["bbox"][3]) - max(bar.y0, line["bbox"][1])
        beside = max((line for line, _ in lines if overlap(line) > 0), key=overlap, default=None)
        # Take the text line's height so the bar groups into that line's row.
        y0, y1 = (beside["bbox"][1], beside["bbox"][3]) if beside else (bar.y0, bar.y1)
        size = beside["spans"][0]["size"] if beside else bar.height
        glyphs = REDACTION_GLYPH * max(1, round(bar.width / (size * NBSP_WIDTH_EM)))
        bbox = (bar.x0, y0, bar.x1, y1)
        span = {"text": glyphs, "flags": 0, "font": "", "size": size, "bbox": bbox}
        out.append(({"bbox": bbox, "spans": [span]}, REDACTION_BLOCK))
    return out


def drawn_rules(drawings):
    """Lines drawn on the page as (horizontal, vertical) lists of (position, start, end): stroked
    lines, thin filled rectangles and the edges of stroked rectangles. Redaction bars, their
    underlines and junction dots are left out."""
    horizontal, vertical = [], []
    for d in without_redaction_marks(drawings):
        stroked = d.get("color") is not None
        filled = d.get("fill") not in (None, WHITE)
        for item in d["items"]:
            if item[0] == "l" and stroked:
                a, b = item[1], item[2]
                if abs(a.y - b.y) < RULE_MAX_THICKNESS:
                    horizontal.append((a.y, min(a.x, b.x), max(a.x, b.x)))
                elif abs(a.x - b.x) < RULE_MAX_THICKNESS:
                    vertical.append((a.x, min(a.y, b.y), max(a.y, b.y)))
            elif item[0] == "re":
                r = item[1]
                if filled and r.height < RULE_MAX_THICKNESS:
                    horizontal.append(((r.y0 + r.y1) / 2, r.x0, r.x1))
                elif filled and r.width < RULE_MAX_THICKNESS:
                    vertical.append(((r.x0 + r.x1) / 2, r.y0, r.y1))
                elif stroked:
                    horizontal += [(r.y0, r.x0, r.x1), (r.y1, r.x0, r.x1)]
                    vertical += [(r.x0, r.y0, r.y1), (r.x1, r.y0, r.y1)]
    horizontal = [rule for rule in horizontal if rule[2] - rule[1] >= RULE_MIN_LENGTH]
    vertical = [rule for rule in vertical if rule[2] - rule[1] >= RULE_MIN_LENGTH]
    return horizontal, vertical


def has_rule(rules, position, start, end, min_cover=0.0, tolerance=RULE_POSITION_TOLERANCE):
    """Whether a rule lies at `position` alongside the span start..end, covering at least
    `min_cover` of it."""
    for rule_position, rule_start, rule_end in rules:
        overlap = min(end, rule_end) - max(start, rule_start)
        if abs(rule_position - position) <= tolerance and overlap > 0 \
                and overlap >= min_cover * (end - start):
            return True
    return False


def drawn_borders(area, table_area, horizontal, vertical):
    """Sides of a cell (top, bottom, left, right) that the source draws a rule along.

    The table's outer rules may sit up to a cell's padding outside the detected table edge.
    """
    x0, y0, x1, y1 = area

    def tolerance(edge, table_edge):
        return MAX_CELL_PADDING if abs(edge - table_edge) <= RULE_POSITION_TOLERANCE else RULE_POSITION_TOLERANCE

    sides = {"top": has_rule(horizontal, y0, x0, x1, BORDER_MIN_COVER, tolerance(y0, table_area[1])),
             "bottom": has_rule(horizontal, y1, x0, x1, BORDER_MIN_COVER, tolerance(y1, table_area[3])),
             "left": has_rule(vertical, x0, y0, y1, BORDER_MIN_COVER, tolerance(x0, table_area[0])),
             "right": has_rule(vertical, x1, y0, y1, BORDER_MIN_COVER, tolerance(x1, table_area[2]))}
    return {side for side, drawn in sides.items() if drawn}


def source_row_starts(ys, xs, lines, rules):
    """Indexes into `ys` of the grid rows that begin a row of the source table.

    Where no rule is drawn between rows, PyMuPDF guesses one row per text line. A guessed row is
    folded into the one above unless a rule separates them or the text starting in the table's
    first column with any text begins a new entry there instead of wrapping onto the next line.
    """
    columns = [[ln[0]["bbox"] for ln in lines if xs[c] - RULE_POSITION_TOLERANCE <= ln[0]["bbox"][0] < xs[c + 1]]
               for c in range(len(xs) - 1)]
    key_column = next((col for col in columns if col), [])
    line_height = statistics.median(b[3] - b[1] for b in key_column) if key_column else 0
    starts = [0]
    for k in range(1, len(ys) - 1):
        if has_rule(rules, ys[k], xs[0], xs[-1]) \
                or starts_new_entry(key_column, ys[k], ys[k + 1], line_height):
            starts.append(k)
    return starts


def starts_new_entry(column_lines, top, bottom, line_height):
    below = [b for b in column_lines if top - SAME_ROW_TOLERANCE <= b[1] < bottom]
    above = [b for b in column_lines if b[3] <= top + SAME_ROW_TOLERANCE]
    if not below:
        return False
    if not above:
        return True
    gap = min(b[1] for b in below) - max(b[3] for b in above)
    return gap > WRAPPED_LINE_MAX_GAP_SHARE * line_height


def join_cells_across_bars(cells, bars):
    """Join side-by-side cells split at an edge that runs through a redaction bar in some row.

    PyMuPDF splits a line of text where a bar interrupts it, and the pieces look like separate
    columns in every row. A real cell edge never runs through a bar, since bars sit inside cells.
    """
    cells = [list(cell) for cell in cells if cell]
    slack = REDACTION_BAR_STROKE_SLACK

    def right_neighbour(left):
        return next((cell for cell in cells if abs(cell[0] - left[2]) <= slack
                     and abs(cell[1] - left[1]) <= slack and abs(cell[3] - left[3]) <= slack), None)

    false_edges = {round(left[2]) for left in cells if right_neighbour(left)
                   and any(bar.x0 - slack <= left[2] <= bar.x1 + slack and bar.y1 > left[1] and bar.y0 < left[3]
                           for bar in bars)}
    joined = True
    while joined:
        joined = False
        for left in cells:
            right = right_neighbour(left)
            if right and any(abs(left[2] - edge) <= slack for edge in false_edges):
                left[2] = right[2]
                cells.remove(right)
                joined = True
                break
    return cells


def split_cells_at_text_columns(cells, lines):
    """Split cells where the table's text starts a column that no rule is drawn for.

    Tables often rule only their header, so PyMuPDF finds just the header's columns. A cell is
    split at such a column when it has text on both sides and no line runs across it (which
    marks a merged cell).
    """
    edges = {round(c[0], 1) for c in cells} | {round(c[2], 1) for c in cells}
    implied = [x for x in column_starts(line_rows(lines)) if all(abs(x - edge) > COLUMN_START_MERGE for edge in edges)]
    if not implied:
        return cells
    out = []
    for cell in cells:
        cell_lines = [line for line in lines if inside(line[0]["bbox"], cell)]
        cuts = [x for x in implied if cell[0] + COLUMN_MIN_GAP < x < cell[2] - COLUMN_MIN_GAP
                and fits_columns(cell_lines, [x])
                and any(line[0]["bbox"][2] <= x for line in cell_lines)
                and any(line[0]["bbox"][0] >= x - COLUMN_MIN_GAP / 2 for line in cell_lines)]
        bounds = [cell[0]] + cuts + [cell[2]]
        out += [[bounds[i], cell[1], bounds[i + 1], cell[3]] for i in range(len(bounds) - 1)]
    return out


def group_of_each(starts, count):
    """For each of `count` guessed rows, the index of the source row it belongs to."""
    return [bisect.bisect_right(starts, k) - 1 for k in range(count)]


def table_grid(page, table, lines, drawings):
    """Turn a PyMuPDF table into a grid with explicit row/column spans."""
    cells = join_cells_across_bars(table.cells, redaction_bars(drawings))
    cells = split_cells_at_text_columns(cells, lines)
    xs = merge_close(sorted({round(c[0], 1) for c in cells} | {round(c[2], 1) for c in cells}))
    guessed_ys = merge_close(sorted({round(c[1], 1) for c in cells} | {round(c[3], 1) for c in cells}))
    if len(xs) < 2 or len(guessed_ys) < 2:
        return None
    horizontal, vertical = drawn_rules(drawings)
    row_starts = source_row_starts(guessed_ys, xs, lines, horizontal)
    ys = [guessed_ys[k] for k in row_starts] + [guessed_ys[-1]]
    row_of = group_of_each(row_starts, len(guessed_ys) - 1)
    n_cols, n_rows = len(xs) - 1, len(ys) - 1
    table_area = (xs[0], ys[0], xs[-1], ys[-1])
    grid = [[None] * n_cols for _ in range(n_rows)]
    for cell in cells:
        c0, c1 = nearest(xs, cell[0]), nearest(xs, cell[2])
        guessed_r0, guessed_r1 = nearest(guessed_ys, cell[1]), nearest(guessed_ys, cell[3])
        if c1 <= c0 or guessed_r1 <= guessed_r0:
            continue
        r0, r1 = row_of[guessed_r0], row_of[guessed_r1 - 1] + 1
        if grid[r0][c0] is not None:
            continue
        area = (xs[c0], ys[r0], xs[c1], ys[r1])
        cell_lines = [ln for ln in lines if inside(ln[0]["bbox"], area)]
        grid[r0][c0] = {
            "rowspan": r1 - r0,
            "colspan": c1 - c0,
            "paragraphs": cell_paragraphs(cell_lines, page.number + 1),
            "fill": cell_fill(cell, drawings),
            "borders": drawn_borders(area, table_area, horizontal, vertical),
        }
        for r in range(r0, r1):
            for c in range(c0, c1):
                if (r, c) != (r0, c0) and grid[r][c] is None:
                    grid[r][c] = "covered"
    for r in range(n_rows):
        for c in range(n_cols):
            if grid[r][c] is None:
                grid[r][c] = {"rowspan": 1, "colspan": 1, "paragraphs": [], "fill": None,
                              "borders": drawn_borders((xs[c], ys[r], xs[c + 1], ys[r + 1]), table_area,
                                                       horizontal, vertical)}
            elif grid[r][c] == "covered":
                grid[r][c] = None
    widths = [xs[i + 1] - xs[i] for i in range(n_cols)]
    return Table(grid, widths, page.number + 1, page.number + 1, cell_padding(table, lines),
                 table.bbox[3] - table.bbox[1], font_width_ratio=width_ratio_to_times(lines))


def width_ratio_to_times(lines):
    """How wide the text is in the source compared with the same text set in Times (the
    template's font) at the same size, capped at 1. Below 1 for narrow fonts such as Arial
    Narrow, where the same words in Times would wrap onto more lines."""
    source_width = times_width = 0.0
    for line, _ in lines:
        for span in line["spans"]:
            if not span["text"].strip() or REDACTION_GLYPH in span["text"]:
                continue
            bold = bool(span["flags"] & BOLD_FLAG) or "bold" in span["font"].lower()
            source_width += span["bbox"][2] - span["bbox"][0]
            times_width += pymupdf.get_text_length(span["text"], fontname="tibo" if bold else "tiro",
                                                   fontsize=span["size"])
    return min(1.0, source_width / times_width) if times_width else 1.0


def line_rows(lines):
    """PDF lines grouped into visual rows, top to bottom, each sorted left to right."""
    rows = []
    for line in sorted(lines, key=lambda item: (round(item[0]["bbox"][1]), item[0]["bbox"][0])):
        if rows and abs(line[0]["bbox"][1] - rows[-1][0][0]["bbox"][1]) < SAME_ROW_TOLERANCE:
            rows[-1].append(line)
        else:
            rows.append([line])
    for row in rows:
        row.sort(key=lambda item: item[0]["bbox"][0])
    return rows


def line_text(line):
    return "".join(s["text"] for s in line[0]["spans"]).strip()


def column_starts(rows):
    """x positions where a second (or later) column of text starts in several rows: a piece of
    text well clear of the piece before it, not counting text hung after a bullet, list number
    or section number."""
    counts = Counter()
    for row in rows:
        for before, piece in zip(row, row[1:]):
            if is_list_marker(line_text(before)):
                continue
            if piece[0]["bbox"][0] - before[0]["bbox"][2] >= COLUMN_MIN_GAP:
                counts[round(piece[0]["bbox"][0])] += 1
    starts = merge_close([x for x, n in sorted(counts.items()) if n >= MIN_COLUMN_LAYOUT_ROWS],
                         tolerance=COLUMN_START_MERGE)
    # A column's header or label may sit flush left of its indented bullets.
    return [min(x for x in counts if start - COLUMN_START_MERGE <= x <= start) for start in starts]


def is_list_marker(text):
    return text in BULLET_GLYPHS or bool(LIST_MARKER.fullmatch(text) or SECTION_NUMBER.match(text))


def fits_columns(row, boundaries):
    """Whether no piece of the row runs across a column boundary."""
    return not any(line[0]["bbox"][0] < x - COLUMN_MIN_GAP / 2 < line[0]["bbox"][2] - COLUMN_MIN_GAP / 2
                   for line in row for x in boundaries)


def is_multi_column(row, boundaries):
    columns = {bisect.bisect_right(boundaries, line[0]["bbox"][0] + COLUMN_MIN_GAP / 2) for line in row}
    return len(columns) > 1


def column_layout_runs(rows, boundaries, taken):
    """Runs of rows laid out in the given columns, trimmed to start at a row with text in more
    than one column and to end at one, or at a line wrapped onto from it."""
    runs, current = [], []

    def close():
        multi = [i for i, row in enumerate(current) if is_multi_column(row, boundaries)]
        if len(multi) >= MIN_COLUMN_LAYOUT_ROWS:
            end = multi[-1] + 1
            line_height = current[multi[-1]][0][0]["bbox"][3] - current[multi[-1]][0][0]["bbox"][1]
            while end < len(current):
                gap = current[end][0][0]["bbox"][1] - current[end - 1][0][0]["bbox"][3]
                if gap > WRAPPED_LINE_MAX_GAP_SHARE * line_height:
                    break
                end += 1
            runs.append(current[multi[0]:end])
        current.clear()

    for row in rows:
        top = row[0][0]["bbox"][1]
        if current:
            previous = current[-1][0][0]["bbox"]
            line_height = previous[3] - previous[1]
            blocked = any(previous[3] <= rect[1] and rect[3] <= top for rect in taken)
            if blocked or top - previous[3] > MAX_COLUMN_LAYOUT_GAP * line_height:
                close()
        if fits_columns(row, boundaries):
            current.append(row)
        else:
            close()
    close()
    return runs


def column_layout_tables(page, lines, drawings, taken):
    """Tables drawn without column rules: runs of text rows that split into the same columns,
    such as an objectives/endpoints table. Returns (table, area) pairs."""
    rows = line_rows(lines)
    boundaries = column_starts(rows)
    if not boundaries:
        return []
    horizontal, vertical = drawn_rules(drawings)
    out = []
    for run in column_layout_runs(rows, boundaries, taken):
        run_lines = [line for row in run for line in row]
        x0 = min(line[0]["bbox"][0] for line in run_lines)
        x1 = max(line[0]["bbox"][2] for line in run_lines)
        xs = [x0] + [x for x in boundaries if x0 < x < x1] + [x1]
        guessed_ys = [row[0][0]["bbox"][1] for row in run] + [max(line[0]["bbox"][3] for line in run[-1])]
        row_starts = source_row_starts(guessed_ys, xs, run_lines, horizontal)
        ys = [guessed_ys[k] for k in row_starts] + [guessed_ys[-1]]
        table_area = (xs[0], ys[0], xs[-1], ys[-1])
        grid = []
        for r in range(len(ys) - 1):
            grid_row = []
            for c in range(len(xs) - 1):
                area = (xs[c], ys[r], xs[c + 1], ys[r + 1])
                cell_lines = [line for line in run_lines if inside(line[0]["bbox"], area)]
                grid_row.append({"rowspan": 1, "colspan": 1, "fill": None,
                                 "paragraphs": cell_paragraphs(cell_lines, page.number + 1),
                                 "borders": drawn_borders(area, table_area, horizontal, vertical)})
            grid.append(grid_row)
        widths = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
        table = Table(grid, widths, page.number + 1, page.number + 1, (0.0, DEFAULT_CELL_PADDING[1]),
                      ys[-1] - ys[0], font_width_ratio=width_ratio_to_times(run_lines))
        out.append((table, table_area))
    return out


def ruled_text_box(page, rect, drawings, lines):
    """A drawing cluster made only of rules around and between lines of text (a boxed list),
    as a one-column table with a row between each pair of full-width rules. None otherwise."""
    rules_only = [d for d in without_redaction_marks(drawings) if inside(tuple(d["rect"]), rect)]
    if not rules_only or any(min(d["rect"].width, d["rect"].height) >= RULE_MAX_THICKNESS for d in rules_only):
        return None
    box_lines = [line for line in lines if inside(line[0]["bbox"], rect)]
    if len(box_lines) < MIN_COLUMN_LAYOUT_ROWS:
        return None
    horizontal, _ = drawn_rules(drawings)
    width = rect[2] - rect[0]
    ys = merge_close(sorted({rect[1], rect[3]} | {round(y, 1) for y, x0, x1 in horizontal
                                                  if rect[1] < y < rect[3] and x1 - x0 >= FULL_WIDTH_RULE_SHARE * width}))
    cells = [(rect[0], ys[i], rect[2], ys[i + 1]) for i in range(len(ys) - 1)]
    return table_grid(page, DrawnTable(cells, rect), box_lines, drawings)


def has_text(table):
    return any(cell and cell["paragraphs"] for row in table.grid for cell in row)


def merge_close(values, tolerance=2.0):
    out = []
    for v in values:
        if not out or v - out[-1] > tolerance:
            out.append(v)
    return out


def nearest(points, value):
    return min(range(len(points)), key=lambda i: abs(points[i] - value))


def cell_paragraphs(lines, page_no):
    rows = group_rows(lines, page_no)
    return rows_to_paragraphs(rows)


def starts_bold_lead_in(row, previous):
    """A line opening in bold right after a finished sentence in plain text, such as a
    "Risk minimization:" label: a new paragraph even without a gap above it."""
    if previous is None:
        return False
    first = next((s for s in row.spans() if s.text.strip()), None)
    last = next((s for s in reversed(previous.spans()) if s.text.strip()), None)
    return bool(first and last and first.bold and not last.bold
                and last.text.rstrip()[-1:] in SENTENCE_END)


def starts_with_label(row):
    spans = [s for s in row.spans() if s.text.strip()]
    return bool(spans) and spans[0].bold and ":" in "".join(s.text for s in spans if s.bold)


def rows_to_paragraphs(rows, break_on_labels=False):
    paragraphs = []
    line_heights = [r.height for r in rows if r.height > 0]
    typical = statistics.median(line_heights) if line_heights else 12
    previous = None
    for row in rows:
        first = row.first_piece_text()
        bullet = first in BULLET_GLYPHS and len(row.pieces) > 1
        if not bullet and first[:1] in BULLET_GLYPHS - {"o", "-", "–"} and len(first) > 1:
            bullet = True
            row = strip_leading_glyph(row)
        new_paragraph = (
            previous is None
            or bullet
            or row.block != previous.block and row.y0 - previous.y1 > 0.2 * typical
            or row.y0 - previous.y1 > (PARAGRAPH_GAP_FACTOR - 1) * typical
            or starts_enumeration(row.text)
            or starts_bold_lead_in(row, previous)
            or (break_on_labels and starts_with_label(row))
        )
        if new_paragraph:
            spans = row.spans(skip_pieces=1 if bullet and first in BULLET_GLYPHS else 0)
            kind = "bullet" if bullet else "body"
            paragraphs.append(Paragraph(spans, kind, page=row.page, first_x=row.x0))
        else:
            join_spans(paragraphs[-1].spans, row.spans())
        previous = row
    for para in paragraphs:
        para.spans = tidy_spans(para.spans)
    return [p for p in paragraphs if p.spans]


def tidy_spans(spans):
    """Collapse whitespace runs (including across span boundaries) and trim the paragraph ends."""
    out = []
    ends_with_space = True
    for s in spans:
        text = re.sub(r"\s+", " ", s.text)
        if ends_with_space:
            text = text.lstrip(" ")
        if not text:
            continue
        out.append(Span(text, s.bold, s.italic, s.superscript, s.size))
        ends_with_space = text.endswith(" ")
    while out and out[-1].text.endswith(" "):
        out[-1].text = out[-1].text.rstrip(" ")
        if not out[-1].text:
            out.pop()
    return out


def strip_leading_glyph(row):
    x, spans = row.pieces[0]
    spans = copy.deepcopy(spans)
    spans[0].text = spans[0].text.lstrip()[1:].lstrip()
    return Row(row.page, row.x0, row.y0, row.y1, row.block, [(x, spans)] + row.pieces[1:])


def starts_enumeration(text):
    return bool(re.match(r"^(\(?[a-z0-9]{1,3}[.)]|\[\d+\])\s+\S", text))


def join_spans(target, more):
    if not more:
        return
    last = target[-1].text if target else ""
    if last.endswith("-") and not last.endswith(" -"):
        target.extend(more)
        return
    target.append(Span(" ", False, False, False, 0))
    target.extend(more)


def vector_figures(page, drawings, table_rects):
    """Clusters of vector drawings big enough to be a figure (or a block redaction).

    Inline redaction bars are left out: they belong to the text around them.
    """
    drawings = without_redaction_marks(drawings)
    if not drawings:
        return []
    figures = []
    for rect in page.cluster_drawings(drawings=drawings):
        rect = tuple(rect)
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        if width * height < MIN_VECTOR_FIGURE_AREA or in_margin_zone(rect, page):
            continue
        if any(overlaps(rect, t) for t in table_rects):
            continue
        figures.append(rect)
    return figures


def is_redaction(page, drawings, rect):
    words = page.get_text("words", clip=pymupdf.Rect(rect))
    if words and all(w[4] == REDACTION_MARK for w in words):
        return True
    area = (rect[2] - rect[0]) * (rect[3] - rect[1])
    filled = 0.0
    for d in drawings:
        r = d["rect"]
        if d.get("fill") == BLACK and inside(tuple(r), rect):
            filled += r.width * r.height
    return filled >= REDACTION_FILL_SHARE * area


def render_clip(page, rect):
    pix = page.get_pixmap(clip=pymupdf.Rect(rect), dpi=FIGURE_DPI)
    return pix.tobytes("png")


def read_pdf(path):
    pdf = pymupdf.open(path)
    boilerplate = find_boilerplate(pdf)
    items = []  # (page_no, y, kind, payload) in reading order
    toc_pages = set()
    for page in pdf:
        page_no = page.number + 1
        blocks = page.get_text("dict")["blocks"]
        page_lines = [(line, block["number"]) for block in blocks for line in block.get("lines", [])
                      if "".join(s["text"] for s in line["spans"]).strip()]
        lines = [(line, block_no) for line, block_no in page_lines
                 if not is_running_header(line, page, page_lines, boilerplate)]
        landscape = page.rect.width > page.rect.height
        items.append((page_no, -1, "page", PageMark(landscape, page_no)))
        all_rows = group_rows(lines, page_no)
        if is_toc_page(all_rows):
            # Contents pages are replaced by a TOC field; keep only their rows so headings like
            # "TABLE OF CONTENTS" still switch regions.
            toc_pages.add(page_no)
            items.extend((page_no, row.y0, "row", row) for row in all_rows)
            continue

        drawings = page.get_drawings()
        lines = lines + redaction_bar_lines(drawings, lines)
        tables = [t for t in find_tables(page, drawings) if t.row_count >= 2 or t.col_count >= 2]
        table_rects = [tuple(t.bbox) for t in tables]
        taken = list(table_rects)
        for t in tables:
            grid = table_grid(page, t, [ln for ln in lines if inside(ln[0]["bbox"], t.bbox)], drawings)
            if grid and has_text(grid):
                items.append((page_no, t.bbox[1], "table", grid))

        for block in blocks:
            if block["type"] != 1:
                continue
            rect = tuple(block["bbox"])
            if rect[2] - rect[0] < MIN_FIGURE_SIDE or rect[3] - rect[1] < MIN_FIGURE_SIDE:
                continue
            if in_margin_zone(rect, page) or any(overlaps(rect, t) for t in taken):
                continue
            taken.append(rect)
            items.append((page_no, rect[1], "figure", Figure(render_clip(page, rect), rect[2] - rect[0], page_no)))
        for rect in vector_figures(page, drawings, taken):
            taken.append(rect)
            box = ruled_text_box(page, rect, drawings, lines)
            if box:
                items.append((page_no, rect[1], "table", box))
            elif is_redaction(page, drawings, rect):
                items.append((page_no, rect[1], "redaction", Redaction(page_no)))
            else:
                items.append((page_no, rect[1], "figure", Figure(render_clip(page, rect), rect[2] - rect[0], page_no)))

        free_lines = [ln for ln in lines if not any(inside(ln[0]["bbox"], r) for r in taken)]
        if page_no > TITLE_PAGES:
            for table, area in column_layout_tables(page, free_lines, drawings, taken):
                items.append((page_no, area[1], "table", table))
                taken.append(area)
            free_lines = [ln for ln in free_lines if not any(inside(ln[0]["bbox"], r) for r in taken)]
        for row in group_rows(free_lines, page_no):
            items.append((page_no, row.y0, "row", row))
    items.sort(key=lambda item: (item[0], item[1]))
    return items, toc_pages


# ---------------------------------------------------------------- structure

def skipped_numbers(previous, number):
    """Section numbers missing between `previous` and `number`, or None if `number` can't follow it.

    An empty list means `number` is the direct successor.
    """
    parts = [int(p) for p in number.split(".")]
    prev = [int(p) for p in previous.split(".")] if previous else []
    parent = parts[:-1]
    if len(parent) > len(prev) or prev[:len(parent)] != parent:
        return None
    start = prev[len(parent)] + 1 if len(prev) > len(parent) else 1
    if parts[-1] < start:
        return None
    return [".".join(map(str, parent + [k])) for k in range(start, parts[-1])]


def heading_candidate(row, last_number):
    """A row starting with a section number followed by title text, eg '2.1' + 'Study Rationale'.

    Returns (number, title, combined); combined is True when number and title share one text run.
    Lettered appendices ('Appendix A ...', then 'A 1' + title) are numbered under the current
    level-1 section, the way CPT numbers its appendices (10.1, 10.1.1, ...).
    """
    first = row.first_piece_text()
    rest = " ".join("".join(s.text for s in spans).strip() for _, spans in row.pieces[1:]).strip()
    chapter = last_number.split(".")[0] if last_number else None
    match = SECTION_NUMBER.match(first)
    appendix = APPENDIX_HEADING.match(row.text)
    appendix_sub = LETTERED_SUBSECTION.match(first)
    if match and rest:
        number, title, combined = match.group(1), rest, False
    elif appendix and chapter:
        number = f"{chapter}.{letter_index(appendix.group(1))}"
        title, combined = row.text.strip(), True
    elif appendix_sub and rest and chapter:
        number = f"{chapter}.{letter_index(appendix_sub.group(1))}.{appendix_sub.group(2)}"
        title, combined = rest, False
    else:
        match = COMBINED_HEADING.match(row.text)
        if not match:
            return None
        number, title, combined = match.group(1), match.group(2).strip(), True
    if not title or TOC_LEADER.search(title) or not re.match(r"[A-Za-z\[(]", title):
        return None
    return number, title, combined


def letter_index(letter):
    return ord(letter.upper()) - ord("A") + 1


def structure(items, toc_pages, body_size):
    doc = Document()
    landscape_pages = {page_no: payload.landscape for page_no, _, kind, payload in items if kind == "page"}
    region = "title"
    last_number = None
    pending_rows = []
    heading_x = {}  # left edge of headings, per page orientation

    def flush():
        if not pending_rows:
            return
        rows = list(pending_rows)
        pending_rows.clear()
        target = {"title": doc.title_rows, "front": doc.front, "skip": None, "body": doc.body}[region]
        if target is None:
            return
        if region == "title":
            target.extend(rows)
        else:
            for p in rows_to_paragraphs(rows):
                target.append(p)

    i = 0
    while i < len(items):
        page_no, _, kind, payload = items[i]
        i += 1
        if kind == "page":
            flush()
            if region == "body":
                doc.body.append(payload)
            continue
        if kind != "row":
            flush()
            if region == "title":
                if kind == "table":
                    doc.title_tables.append(payload)
            elif region == "front":
                doc.front.append(payload)
            elif region == "body":
                doc.body.append(payload)
            continue

        row = payload
        candidate = heading_candidate(row, last_number)
        skipped = skipped_numbers(last_number, candidate[0]) if candidate else None
        emphasized = row.is_bold() or row.size() > body_size
        landscape = landscape_pages[page_no]
        x_ok = landscape not in heading_x or abs(row.x0 - heading_x[landscape]) < HEADING_X_TOLERANCE
        styled_differently = emphasized or row.is_italic()
        if candidate and skipped is not None and page_no not in toc_pages and x_ok \
                and (emphasized or (not candidate[2] and "." in candidate[0])
                     or (styled_differently and not skipped and candidate[0].count(".") >= 2)) \
                and (not skipped or (emphasized and len(skipped) <= MAX_SKIPPED_HEADINGS)):
            heading_x.setdefault(landscape, payload.x0)
            flush()
            number, title, combined = candidate
            # Long headings wrap onto following rows of the same block, aligned with the title text
            # (or anywhere in the hanging indent when number and title share one run).
            title_x = None if combined else row.pieces[1][0]
            while i < len(items) and items[i][2] == "row":
                nxt = items[i][3]
                aligned = (row.x0 - 2 <= nxt.x0 <= row.x0 + WRAPPED_TITLE_MAX_INDENT) if combined \
                    else abs(nxt.x0 - title_x) < 4
                if nxt.block == row.block and nxt.page == row.page and aligned \
                        and nxt.y0 - row.y1 < row.height and nxt.is_bold() == row.is_bold():
                    title += " " + nxt.text
                    i += 1
                    row = nxt
                else:
                    break
            if region != "body":
                region = "body"
                doc.body.append(PageMark(landscape, page_no))
            for missing in skipped:
                doc.body.append(Heading(missing, MISSING_HEADING_TITLE, page_no))
            doc.body.append(Heading(number, title, page_no))
            last_number = number
            continue

        if region in ("title", "front", "skip") and is_front_heading(row, body_size):
            flush()
            if SKIPPED_FRONT_MATTER.match(row.text.strip()):
                region = "skip"
            else:
                region = "front"
                doc.front.append(FrontHeading(row.text.strip().title() if row.text.isupper() else row.text.strip(), page_no))
            continue
        if region == "skip" and page_no not in toc_pages and not TOC_LEADER.search(row.text):
            # The contents list ended without a new heading; resume front matter.
            if row.page not in toc_pages:
                region = "front"
        if page_no in toc_pages:
            continue
        pending_rows.append(row)
    flush()
    return doc


def is_front_heading(row, body_size):
    text = TOC_LEADER.sub("", row.text).strip()
    if TOC_LEADER.search(row.text):
        return False
    if not (row.is_bold() or row.size() > body_size + 0.5):
        return False
    return bool(FRONT_MATTER_HEADINGS.match(text))


def body_font_size(items):
    sizes = Counter()
    for _, _, kind, payload in items:
        if kind == "row":
            for _, spans in payload.pieces:
                for s in spans:
                    sizes[s.size] += len(s.text)
    return sizes.most_common(1)[0][0] if sizes else 12


# ---------------------------------------------------------------- title page

def title_fields(rows):
    """Split title-page text into [label, value] pairs; unlabeled paragraphs get label None."""
    value_x = value_column_x(rows)
    if value_x is not None:
        return two_column_fields(rows, value_x)
    fields = []
    for para in rows_to_paragraphs(rows, break_on_labels=True):
        for spans in split_at_labels(para.spans):
            text = "".join(s.text for s in spans).strip()
            label, value = split_label(spans)
            if label:
                fields.append([label, value])
            elif fields and fields[-1][0] and not fields[-1][1]:
                fields[-1][1] = text
            elif text:
                fields.append([None, text])
    return fields


def split_at_labels(spans):
    """Split a paragraph before each bold 'Label:' that follows other text (two fields on one line)."""
    groups = [[]]
    for s in spans:
        follows_text = any(x.text.strip() for x in groups[-1])
        if s.bold and s.text.strip().endswith(":") and follows_text and not groups[-1][-1].bold:
            groups.append([])
        groups[-1].append(s)
    return groups


def value_column_x(rows):
    """Left edge of the value column when the title page is laid out as label | value columns."""
    starts = Counter(round(piece[0]) for row in rows for piece in row.pieces[1:])
    labels = sum(1 for row in rows if len(row.pieces) > 1 and row.first_piece_text().endswith(":"))
    if not starts or labels < MIN_TWO_COLUMN_LABELS:
        return None
    x, count = starts.most_common(1)[0]
    return x if count >= MIN_TWO_COLUMN_LABELS else None


def two_column_fields(rows, value_x):
    """Pair labels in the left column (which may wrap over several rows, ending in ':') with the
    text beside them in the right column."""
    fields = []
    label_open = False  # the last label hasn't reached its closing ':' yet
    for row in rows:
        left = " ".join("".join(s.text for s in spans).strip() for x, spans in row.pieces if x < value_x - 5).strip()
        right = " ".join("".join(s.text for s in spans).strip() for x, spans in row.pieces if x >= value_x - 5).strip()
        if left and not right and len(left) > MAX_LABEL_LENGTH:
            fields.append([None, left])
            label_open = False
        elif left and label_open:
            fields[-1][0] += " " + left
            fields[-1][1] = (fields[-1][1] + " " + right).strip()
        elif left:
            fields.append([left, right])
            label_open = True
        elif right and fields and fields[-1][0]:
            fields[-1][1] = (fields[-1][1] + " " + right).strip()
        else:
            fields.append([None, right])
        if fields[-1][0] and fields[-1][0].endswith(":"):
            label_open = False
    out = []
    for label, value in fields:
        if label and not label.endswith(":"):
            out.append([None, f"{label} {value}".strip()])
        else:
            out.append([label, value])
    return out


def split_label(spans):
    """('Label:', value) when a paragraph starts with a field label, else (None, None)."""
    text = "".join(s.text for s in spans).strip()
    bold_prefix = ""
    for s in spans:
        if not s.bold:
            break
        bold_prefix += s.text
    rest = text[len(bold_prefix.strip()):].strip() if text.startswith(bold_prefix.strip()) else ""
    bold_prefix = bold_prefix.strip()
    if bold_prefix.endswith(":") and len(bold_prefix) <= MAX_LABEL_LENGTH:
        return bold_prefix, rest
    if bold_prefix and rest.startswith(":") and len(bold_prefix) < MAX_LABEL_LENGTH:
        return bold_prefix + ":", rest[1:].strip()
    match = re.match(r"^([A-Z][\w ()/'.-]{2,60}):\s*(.*)$", text)
    if match and canonical_label(match.group(1)):
        return match.group(1) + ":", match.group(2)
    return None, None


def canonical_label(label):
    plain = label.rstrip(":").strip().lower()
    for key, pattern in TITLE_FIELD_PATTERNS:
        if re.fullmatch(pattern, plain):
            return key
    return None


# ---------------------------------------------------------------- docx output

class Writer:
    def __init__(self, template_path, version):
        self.doc = docx.Document(template_path)
        self.version = version
        self.styles = STYLE_NAMES[version]
        self.body = self.doc.element.body
        self.heading_numpr = self._heading_numbering()
        self.bullet_num_id = self._style_num_id(self.styles["bullet"])
        self.portrait_sect, self.landscape_sect = self._section_templates()
        for child in list(self.body):
            if child.tag != qn("w:sectPr"):
                self.body.remove(child)
        self.final_sect = self.body.find(qn("w:sectPr"))
        self.landscape = False
        self.text_width_pt, self.text_height_pt = self._text_area_of(self.portrait_sect)

    def _heading_numbering(self):
        """numPr to attach to each heading level (v011 numbers headings on the paragraph, not the style)."""
        numbering = {}
        for p in self.doc.paragraphs:
            name = p.style.name
            if name in self.styles["headings"]:
                num_pr = p._p.find(qn("w:pPr") + "/" + qn("w:numPr"))
                level = self.styles["headings"].index(name)
                if num_pr is not None and level not in numbering:
                    numbering[level] = num_pr
        if not numbering:
            return {}
        base = numbering.get(0)
        num_id = base.find(qn("w:numId")).get(qn("w:val"))
        out = {}
        for level in range(len(self.styles["headings"])):
            num_pr = OxmlElement("w:numPr")
            ilvl = OxmlElement("w:ilvl")
            ilvl.set(qn("w:val"), str(level))
            nid = OxmlElement("w:numId")
            nid.set(qn("w:val"), num_id)
            num_pr.append(ilvl)
            num_pr.append(nid)
            out[level] = num_pr
        return out

    def _style_num_id(self, style_name):
        style = self.doc.styles[style_name].element
        num_id = style.find(qn("w:pPr") + "/" + qn("w:numPr") + "/" + qn("w:numId"))
        return num_id.get(qn("w:val")) if num_id is not None else None

    def _section_templates(self):
        portrait = landscape = None
        for sect in self.body.iter(qn("w:sectPr")):
            pg = sect.find(qn("w:pgSz"))
            is_landscape = pg is not None and pg.get(qn("w:orient")) == "landscape"
            if is_landscape and landscape is None:
                landscape = copy.deepcopy(sect)
            if not is_landscape and portrait is None:
                portrait = copy.deepcopy(sect)
        return portrait, landscape

    # -- low-level helpers

    def paragraph(self, style, spans=None, text=None):
        p = self.doc.add_paragraph(style=style)
        self.body.remove(p._p)
        self.final_sect.addprevious(p._p)
        if text is not None:
            p.add_run(text)
        for s in spans or []:
            self.add_run(p, s)
        return p

    @staticmethod
    def add_run(p, span, size=None):
        text = span.text.replace("\t", " ")
        if not text:
            return
        run = p.add_run(text)
        if span.bold:
            run.bold = True
        if span.italic:
            run.italic = True
        if span.superscript:
            run.font.superscript = True
        if size:
            run.font.size = Pt(size)
        if set(text) == {REDACTION_GLYPH}:
            # A zero-width space after each no-break space lets a long bar wrap like the source's.
            run.text = (NBSP + ZERO_WIDTH_SPACE) * len(text)
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:color"), "auto")
            shd.set(qn("w:fill"), "000000")
            run._r.get_or_add_rPr().append(shd)  # after sz, which precedes shd in rPr

    def page_break(self):
        p = self.paragraph(self.styles["body"])
        p.add_run().add_break(docx.enum.text.WD_BREAK.PAGE)

    def set_orientation(self, landscape):
        if landscape == self.landscape or self.landscape_sect is None:
            return
        ending = copy.deepcopy(self.landscape_sect if self.landscape else self.portrait_sect)
        for ref in ending.findall(qn("w:titlePg")):
            ending.remove(ref)
        p = self.paragraph(self.styles["body"])
        p._p.get_or_add_pPr().append(ending)
        self.landscape = landscape
        self.text_width_pt, self.text_height_pt = self._text_area_of(
            self.landscape_sect if landscape else self.portrait_sect)

    @staticmethod
    def _text_area_of(sect):
        """(width, height) in pt between the page margins."""
        pg = sect.find(qn("w:pgSz"))
        mar = sect.find(qn("w:pgMar"))
        width = int(pg.get(qn("w:w"))) - int(mar.get(qn("w:left"))) - int(mar.get(qn("w:right")))
        height = int(pg.get(qn("w:h"))) - int(mar.get(qn("w:top"))) - int(mar.get(qn("w:bottom")))
        return width / TWIPS_PER_PT, height / TWIPS_PER_PT

    def finish_sections(self):
        source = self.landscape_sect if self.landscape else self.portrait_sect
        if source is None:
            return
        for tag in ("w:pgSz", "w:pgMar"):
            old = self.final_sect.find(qn(tag))
            new = copy.deepcopy(source.find(qn(tag)))
            if old is not None and new is not None:
                old.addprevious(new)
                self.final_sect.remove(old)

    # -- content

    def heading(self, heading, warnings):
        level = min(heading.level, len(self.styles["headings"])) - 1
        p = self.paragraph(self.styles["headings"][level], text=heading.title)
        if self.heading_numpr:
            p._p.get_or_add_pPr().insert(1, copy.deepcopy(self.heading_numpr[level]))
        if heading.level > len(self.styles["headings"]):
            warnings.append(f"heading {heading.number} deeper than template supports")

    def body_paragraph(self, para):
        if para.kind == "bullet":
            p = self.paragraph(self.styles["bullet"], para.spans)
            if para.level and self.bullet_num_id:
                num_pr = OxmlElement("w:numPr")
                ilvl = OxmlElement("w:ilvl")
                ilvl.set(qn("w:val"), str(para.level))
                nid = OxmlElement("w:numId")
                nid.set(qn("w:val"), self.bullet_num_id)
                num_pr.append(ilvl)
                num_pr.append(nid)
                p._p.get_or_add_pPr().insert(1, num_pr)
            return p
        if para.kind == "caption":
            return self.paragraph(self.styles["caption"], para.spans)
        return self.paragraph(self.styles["body"], para.spans)

    def table(self, table):
        """Write a table sized like the source: its column widths, font sizes and cell padding,
        scaled down together when the table is wider than the page.

        A table that fit on one source page is kept on one page.
        """
        n_rows, n_cols = len(table.grid), len(table.col_widths)
        t = self.doc.add_table(rows=n_rows, cols=n_cols)
        self.body.remove(t._tbl)
        self.final_sect.addprevious(t._tbl)
        t.style = self.doc.styles["Table Grid"]
        t.autofit = False
        keep_together = table.first_page == table.page
        scale = min(1.0, self.text_width_pt / max(sum(table.col_widths), 1))
        if keep_together:
            scale = min(scale, self.text_height_pt * TABLE_PAGE_HEIGHT_SHARE / max(table.height, 1))
        widths = [Pt(width * scale) for width in table.col_widths]
        for grid_col, width in zip(t._tbl.tblGrid.gridCol_lst, widths):
            grid_col.w = width
        for c, width in enumerate(widths):
            for cell in t.columns[c].cells:
                cell.width = width
        set_cell_margins(t, table.padding[0] * scale, table.padding[1] * scale)
        clear_table_borders(t)
        font_scale = scale * table.font_width_ratio
        font_size = table_font_size(table) * font_scale
        for r, row in enumerate(table.grid):
            keep_with_next = keep_together and r < n_rows - 1
            for c, cell in enumerate(row):
                if cell is None:
                    continue
                target = t.cell(r, c)
                if cell["rowspan"] > 1 or cell["colspan"] > 1:
                    target = target.merge(t.cell(r + cell["rowspan"] - 1, c + cell["colspan"] - 1))
                set_cell_borders(target, cell["borders"])
                if cell["fill"]:
                    shade_cell(target, cell["fill"])
                first = True
                for para in cell["paragraphs"]:
                    p = target.paragraphs[0] if first else target.add_paragraph()
                    first = False
                    p.style = self.doc.styles[self.styles["bullet"] if para.kind == "bullet" else self.styles["table_text"]]
                    for s in para.spans:
                        run_size = font_size if s.superscript or not s.size else s.size * font_scale
                        self.add_run(p, s, size=run_size)
                if first:
                    target.paragraphs[0].style = self.doc.styles[self.styles["table_text"]]
                for p in target.paragraphs:
                    compact_cell_paragraph(p, font_size, keep_with_next)
                    if p.style.name == self.styles["bullet"]:
                        # The list style's half-inch indent would squeeze narrow columns.
                        hang = Pt(TABLE_BULLET_HANG_EM * font_size)
                        p.paragraph_format.left_indent = hang
                        p.paragraph_format.first_line_indent = -hang
        for row in t.rows:
            row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for row in t.rows[:table.header_rows]:
            row._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
        self.paragraph(self.styles["body"])

    def figure(self, figure):
        width = min(figure.width_pt, self.text_width_pt)
        p = self.paragraph(self.styles["body"])
        p.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(io.BytesIO(figure.png), width=Pt(width))

    def toc(self):
        """Mark where the table of contents goes; fill_toc writes it once the headings are known."""
        self.paragraph(self.styles["field_label"], text="Table of Contents")
        self.toc_anchor = self.paragraph(self.styles["body"])

    def fill_toc(self, entries):
        """Write a TOC field whose cached result already lists the headings (without page numbers).

        Word shows the cached entries as-is; updating the field adds page numbers. This avoids
        w:updateFields, which makes Word prompt on every open.
        """
        entries = [e for e in entries if e[0] <= TOC_DEPTH] or [(1, "", "")]
        anchor = self.toc_anchor._p
        paragraphs = []
        for level, number, title in entries:
            p = self.doc.add_paragraph(style=f"toc {level}")
            self.body.remove(p._p)
            paragraphs.append(p)
            if number:
                p.add_run(f"{number}\t{title}")
        first, last = paragraphs[0], paragraphs[-1]
        start = first._p.makeelement(qn("w:r"), {})
        start.append(field_char("begin"))
        instr = OxmlElement("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = f' TOC \\o "1-{TOC_DEPTH}" \\h \\z \\u '
        start.append(instr)
        start.append(field_char("separate"))
        first._p.get_or_add_pPr().addnext(start)
        end =last._p.makeelement(qn("w:r"), {})
        end.append(field_char("end"))
        last._p.append(end)
        for p in reversed(paragraphs):
            anchor.addnext(p._p)
        anchor.getparent().remove(anchor)

    def footers(self, label):
        for section in self.doc.sections:
            for part in (section.footer, section.first_page_footer, section.even_page_footer,
                         section.header, section.first_page_header, section.even_page_header):
                if part.is_linked_to_previous:
                    continue
                for p in part.paragraphs:
                    clear_paragraph_text(p)
                for tbl in list(part._element.findall(qn("w:tbl"))):
                    part._element.remove(tbl)
            footer = section.footer
            if not footer.is_linked_to_previous:
                p = footer.paragraphs[0]
                p.add_run(f"{label}\t")
                add_page_field(p)

    def save(self, path):
        self.doc.save(path)


def table_font_size(table):
    sizes = [s.size for row in table.grid for cell in row if cell
             for para in cell["paragraphs"] for s in para.spans if s.size and not s.superscript]
    return statistics.median(sizes) if sizes else DEFAULT_TABLE_FONT_SIZE


# Child order the OOXML schema requires inside w:tblPr and w:tcPr.
TABLE_PROPERTY_ORDER = ["tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize",
                        "tblStyleColBandSize", "tblW", "jc", "tblCellSpacing", "tblInd", "tblBorders",
                        "shd", "tblLayout", "tblCellMar", "tblLook"]
CELL_PROPERTY_ORDER = ["cnfStyle", "tcW", "gridSpan", "hMerge", "vMerge", "tcBorders", "shd", "noWrap",
                       "tcMar", "textDirection", "tcFitText", "vAlign", "hideMark"]


def insert_property(parent, element, order):
    """Insert `element` into a properties element at the position the schema requires."""
    position = order.index(element.tag.split("}")[1])
    for child in parent:
        name = child.tag.split("}")[1]
        if name in order and order.index(name) > position:
            child.addprevious(element)
            return
    parent.append(element)


def border_element(tag, sides):
    """A w:tblBorders/w:tcBorders element with a thin single line on `sides` and none elsewhere."""
    borders = OxmlElement(tag)
    for side in ("top", "left", "bottom", "right") + (("insideH", "insideV") if tag == "w:tblBorders" else ()):
        el = OxmlElement(f"w:{side}")
        if side in sides:
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), str(BORDER_EIGHTHS_OF_PT))
            el.set(qn("w:space"), "0")
            el.set(qn("w:color"), "auto")
        else:
            el.set(qn("w:val"), "nil")
        borders.append(el)
    return borders


def set_cell_margins(table, horizontal_pt, vertical_pt):
    margins = OxmlElement("w:tblCellMar")
    for side, size in (("top", vertical_pt), ("left", horizontal_pt),
                       ("bottom", vertical_pt), ("right", horizontal_pt)):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:w"), str(round(size * TWIPS_PER_PT)))
        el.set(qn("w:type"), "dxa")
        margins.append(el)
    insert_property(table._tbl.tblPr, margins, TABLE_PROPERTY_ORDER)


def clear_table_borders(table):
    """Turn off the table style's grid; each cell then carries the borders the source draws."""
    insert_property(table._tbl.tblPr, border_element("w:tblBorders", set()), TABLE_PROPERTY_ORDER)


def set_cell_borders(cell, sides):
    insert_property(cell._tc.get_or_add_tcPr(), border_element("w:tcBorders", sides), CELL_PROPERTY_ORDER)


def shade_cell(cell, hex_fill):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    insert_property(cell._tc.get_or_add_tcPr(), shd, CELL_PROPERTY_ORDER)


def compact_cell_paragraph(p, font_size, keep_with_next):
    """Single-spaced with no paragraph spacing, so rows are as tall as the source's.

    Table Grid sets a 15 pt minimum line height that Table Text doesn't override.
    """
    fmt = p.paragraph_format
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(0)
    fmt.line_spacing = 1.0
    fmt.keep_with_next = keep_with_next
    mark = OxmlElement("w:rPr")  # paragraph mark properties; last child of pPr
    p._p.get_or_add_pPr().append(mark)
    for tag in ("w:sz", "w:szCs"):
        el = OxmlElement(tag)
        el.set(qn("w:val"), str(round(font_size * 2)))
        mark.append(el)


def clear_paragraph_text(p):
    for child in list(p._p):
        if child.tag != qn("w:pPr"):
            p._p.remove(child)


def field_char(kind):
    el = OxmlElement("w:fldChar")
    el.set(qn("w:fldCharType"), kind)
    return el


def add_page_field(p):
    run = p.add_run()
    for kind, text in (("begin", None), (None, " PAGE "), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._r.append(el)


# ---------------------------------------------------------------- assembly

def classify_paragraphs(blocks):
    """Mark captions and set bullet nesting from indentation."""
    bullet_x = []
    for item in blocks:
        if not isinstance(item, Paragraph):
            continue
        if item.kind == "bullet":
            bullet_x.append(round(item.first_x))
    base = min(bullet_x) if bullet_x else 0
    for item in blocks:
        if not isinstance(item, Paragraph):
            continue
        if item.kind == "bullet":
            item.level = 1 if item.first_x - base >= BULLET_INDENT_STEP else 0
        elif CAPTION.match(item.text) and len(item.text) <= MAX_CAPTION_LENGTH and ". " not in item.text[:-1]:
            item.kind = "caption"


def join_across_pages(blocks):
    """Rejoin paragraphs that a page break split mid-sentence."""
    out = []
    last_para = None
    for item in blocks:
        if isinstance(item, PageMark):
            out.append(item)
            continue
        if isinstance(item, Paragraph) and last_para is not None and item.page == last_para.page + 1 \
                and item.kind == "body" and last_para.kind in ("body", "bullet") \
                and last_para.text and last_para.text[-1] not in ".:;!?)" \
                and item.text[:1].islower():
            join_spans(last_para.spans, item.spans)
            last_para.page = item.page
            continue
        out.append(item)
        last_para = item if isinstance(item, Paragraph) else None
    return out


def merge_split_tables(blocks):
    """Join a table continued on the next page (same columns, nothing in between but a page
    break), and a separately ruled header with the table body right below it."""
    out = []
    for item in blocks:
        if isinstance(item, Table):
            previous = [x for x in out if not isinstance(x, PageMark)]
            prev = previous[-1] if previous else None
            between = out[out.index(prev) + 1:] if prev is not None else []
            adjacent = isinstance(prev, Table) and len(prev.col_widths) == len(item.col_widths) \
                and all(isinstance(x, PageMark) for x in between)
            if adjacent and item.page == prev.page and len(prev.grid) <= MAX_SEPARATE_HEADER_ROWS:
                # A header ruled on its own above a body laid out without rules: one table, with
                # the body's columns.
                prev.grid.extend(item.grid)
                prev.col_widths = item.col_widths
                prev.height += item.height
                continue
            if adjacent and item.page == prev.page + 1:
                repeated = repeated_header_rows(prev.grid, item.grid)
                prev.header_rows = max(prev.header_rows, repeated)
                prev.grid.extend(item.grid[repeated:])
                prev.page = item.page
                continue
        out.append(item)
    return out


def repeated_header_rows(first, continuation):
    """How many leading rows of a continued table repeat the leading rows of its first part."""
    count = 0
    while count < min(len(first), len(continuation)) - 1 \
            and same_row_text(first[count], continuation[count]):
        count += 1
    return count


def collapse_redactions(blocks):
    out = []
    for item in blocks:
        previous = next((x for x in reversed(out) if not isinstance(x, PageMark)), None)
        if isinstance(item, Redaction) and isinstance(previous, Redaction):
            continue
        out.append(item)
    return out


def same_row_text(a, b):
    def text(row):
        return [" ".join(p.text for p in c["paragraphs"]) if c else None for c in row]
    return text(a) == text(b)


def convert(pdf_path, template_path, out_path, version):
    items, toc_pages = read_pdf(pdf_path)
    body_size = body_font_size(items)
    doc = structure(items, toc_pages, body_size)
    warnings = []

    doc.front = collapse_redactions(merge_split_tables(join_across_pages(doc.front)))
    doc.body = collapse_redactions(merge_split_tables(join_across_pages(doc.body)))
    classify_paragraphs(doc.front)
    classify_paragraphs(doc.body)

    writer = Writer(template_path, version)
    styles = writer.styles

    # Title page
    writer.paragraph(styles["page_title"], text="Title Page")
    protocol_id = None
    for label, value in title_fields(doc.title_rows):
        if label is None:
            writer.paragraph(styles["body"], text=value)
            continue
        key = canonical_label(label)
        if key == "protocol_number" and not protocol_id:
            protocol_id = value
        writer.paragraph(styles["field_label"], text=CPT_TITLE_LABELS[version].get(key, label) if key else label)
        if value:
            writer.paragraph(styles["body"], text=value)
    for table in doc.title_tables:
        writer.table(table)
    writer.page_break()

    for item in doc.front:
        if isinstance(item, FrontHeading):
            writer.paragraph(styles["front_heading"], text=item.title)
        elif isinstance(item, Paragraph):
            writer.body_paragraph(item)
        elif isinstance(item, Table):
            writer.table(item)
        elif isinstance(item, Figure):
            writer.figure(item)
        elif isinstance(item, Redaction):
            writer.paragraph(styles["body"], text="[Redacted: CCI]")
    writer.page_break()
    writer.toc()
    writer.page_break()

    counters = []
    toc_entries = []
    for item in doc.body:
        if isinstance(item, PageMark):
            writer.set_orientation(item.landscape)
        elif isinstance(item, Heading):
            expected = advance(counters, item.level)
            if expected != item.number:
                warnings.append(f"p{item.page}: heading {item.number} would auto-number as {expected}")
            toc_entries.append((item.level, expected, item.title))
            writer.heading(item, warnings)
        elif isinstance(item, Paragraph):
            writer.body_paragraph(item)
        elif isinstance(item, Table):
            writer.table(item)
        elif isinstance(item, Figure):
            writer.figure(item)
        elif isinstance(item, Redaction):
            writer.paragraph(styles["body"], text="[Redacted: CCI]")
    writer.finish_sections()
    writer.fill_toc(toc_entries)
    writer.footers(protocol_id or "")
    writer.save(out_path)

    tables = sum(isinstance(x, Table) for x in doc.body + doc.front)
    figures = sum(isinstance(x, Figure) for x in doc.body + doc.front)
    print(f"{pdf_path}: {len(toc_entries)} headings,{tables} tables, {figures} figures, "
          f"{len(toc_pages)} TOC pages skipped -> {out_path}")
    for w in warnings:
        print("  warning:", w)
    return doc


def advance(counters, level):
    del counters[level:]
    while len(counters) < level:
        counters.append(0)
    counters[-1] += 1
    return ".".join(str(c) for c in counters)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--template", required=True)
    parser.add_argument("--version", choices=sorted(STYLE_NAMES), help="CPT version; guessed from the template name")
    parser.add_argument("pdf")
    parser.add_argument("out")
    args = parser.parse_args()
    version = args.version or ("v011" if "11" in args.template.rsplit("/", 1)[-1] else "v010")
    convert(args.pdf, args.template, args.out, version)


if __name__ == "__main__":
    sys.exit(main())
