"""Rebuild a protocol PDF as a .docx on a TransCelerate CPT template.

The output keeps the template's styles, numbering, page setup and section
layout, and fills the body with the PDF's own content: title page fields,
front matter, numbered headings mapped to the template's heading styles,
paragraphs, bullets, tables (with merged cells) and figures.

Usage:
    python pdf_to_cpt_docx.py --template CPT_CoreBWE_v010.docx in.pdf out.docx

Needs PyMuPDF and python-docx.
"""

import argparse
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
from docx.image.image import Image

BOLD_FLAG = 16
ITALIC_FLAG = 2
SUPERSCRIPT_FLAG = 1

PAGE_MARGIN_ZONE = 0.12  # top/bottom fraction of a page treated as running header/footer
BOILERPLATE_MIN_PAGE_SHARE = 0.25  # a margin line repeated on this share of pages is a header/footer
SAME_ROW_TOLERANCE = 3.0  # pt; lines whose tops differ by less sit on the same row
PARAGRAPH_GAP_FACTOR = 1.6  # a vertical gap this many line heights apart starts a new paragraph
BULLET_INDENT_STEP = 12.0  # pt of extra indent that makes a bullet one level deeper
TOC_LEADER = re.compile(r"\.{5,}\s*\d*\s*$|\.{5,}")
TOC_PAGE_MIN_LEADER_SHARE = 0.25
MIN_FIGURE_SIDE = 40.0  # pt
MIN_VECTOR_FIGURE_AREA = 12000.0  # pt^2
REDACTION_FILL_SHARE = 0.8
REDACTION_MARK = "CCI"  # label sponsors print on redacted (commercially confidential) blocks
BLACK = (0.0, 0.0, 0.0)
WHITE = (1.0, 1.0, 1.0)
REDACTION_BAR_HEIGHT_RANGE = (4.0, 16.0)  # pt; a black box about one text line tall is an inline redaction
DEFAULT_CELL_PADDING = (5.0, 2.0)  # pt (horizontal, vertical) when a table has no text to measure
MAX_CELL_PADDING = 10.0  # pt; a wider gap is centred or indented text, not padding
REDACTION_BAR_STROKE_SLACK = 1.0  # pt; a bar's outline sticks out past its rect
CELL_FILL_MIN_SHARE = 0.5  # a fill must cover this share of a cell to count as its shading
DEFAULT_TABLE_FONT_SIZE = 10.0  # pt, for a table with no measurable text
TWIPS_PER_PT = 20
CELL_BULLET_INDENT_PT = 8.0  # the template bullet indent would leave narrow table columns no room for text
TABLE_PAGE_HEIGHT_SHARE = 0.84  # a one-page table may fill this much of the text height; the rest holds its caption and rows that wrap taller than the source
FIGURE_DPI = 150
SCANNED_PAGE_VERTICAL_ALLOWANCE = 18.0  # pt for the image paragraph's spacing and baseline
TOC_DEPTH = 3
MAX_CAPTION_LENGTH = 220
MAX_LABEL_LENGTH = 80
MIN_TWO_COLUMN_LABELS = 3  # label rows needed before a title page is read as label | value columns
TITLE_PAGE_LAST = 1  # later pages in the title region are front matter (e.g. an investigator agreement)
ELECTRONIC_SIGNATURE_VALUE = re.compile(r"^see appended electronic signature page\.?$", re.IGNORECASE)
DATE_LABEL = re.compile(r"\bdate( of issue)?:?$", re.IGNORECASE)
MEDICAL_MONITOR_STATEMENT = re.compile(r"^medical monitor name and contact information (can|will)\b", re.IGNORECASE)
COVER_HEADING = re.compile(r"^(title page|(amended )?clinical (trial|study) protocol( \d+)?)$", re.IGNORECASE)

SECTION_NUMBER = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,5})\.?$")
COMBINED_HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,5})\.?\s+([A-Z].*)$")
MAX_SKIPPED_HEADINGS = 3  # sections a heading may skip (eg redacted ones) and still count as the next heading
MISSING_HEADING_TITLE = "[Heading not present in source PDF]"
REDACTED_HEADING_TITLE = "[REDACTED]"  # a heading whose number is printed but whose title is a black bar
REDACTED_TITLES = {REDACTION_MARK, REDACTED_HEADING_TITLE}
WRAPPED_TITLE_MAX_INDENT = 60.0  # pt
WRAPPED_TITLE_X_TOLERANCE = 16.0  # pt a wrapped title line may differ from the first title line's left edge
WRAPPED_TITLE_SIZE_TOLERANCE = 0.5  # pt
HEADING_X_TOLERANCE = 30.0  # pt a heading may sit right of the first heading's left edge
APPENDIX_HEADING = re.compile(r"^Appendix\s+([A-Z])\b[\s:.]+\S")
LETTERED_SUBSECTION = re.compile(r"^([A-Z])\s?(\d{1,2}(?:\.\d{1,2}){0,3})$")
BULLET_GLYPHS = {"•", "", "", "", "", "o", "▪", "■", "–", "-", "○", "◦", "§", "", "•", "", "✓"}
CAPTION = re.compile(r"^(Table|Figure)\s+[\dA-Z][\w.\-]*:?\s+\S")

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
    ("acronym", r"(trial |study )?acronym|(trial|study) name(/acronym)?"),
    ("protocol_number", r"(clinical (trial|study) )?protocol (number|identifier|no\.?)|sponsor protocol identifier|study (number|identifier)|protocol identifier|trial id"),
    ("amendment_number", r"amendment (number|identifier|no\.?)"),
    ("version", r"(protocol )?version( number)?"),
    ("date", r"(version |approval |protocol |release )?date|date of issue"),
    ("compound", r"compound( number| name)?(/name)?( \(.*\))?|investigational product( code)?\(?s?\)?|substance name|test product|study (drug|intervention)"),
    ("phase", r"(study|trial|clinical) phase|phase"),
    ("sponsor", r"sponsor( name)?( and (legal )?(registered )?address)?"),
    ("sponsor_address", r"legal registered address|sponsor address"),
    ("manufacturer", r"manufacturer"),
    ("regulatory_ids", r"regulatory agency identifier number\(?s?\)?|regulatory or clinical trial identifier\(?s?\)?|registry identifiers?|regulatory identifier"),
    ("registry_id", r"other|(ind|nct|who|eudamed|eudract|eu ct|ctis|isrctn)( trial)?( number| no\.?)?|eu trial number|eu ct number|eudract/eu ct number"),
    ("signatory", r"sponsor signatory"),
    ("medical_monitor", r"medical (monitor|expert).*|(study )?medical\s*(/\s*study)?\s*director(\s*/\s*medical monitor)?|monitoring team.*"),
    ("primary_intervention", r"primary study intervention\(s\)"),
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

    def starts_bold(self):
        first = next((s for _, spans in self.pieces for s in spans if s.text.strip()), None)
        return bool(first) and first.bold

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


@dataclass
class Figure:
    png: bytes
    width_pt: float
    page: int
    full_page: bool = False  # a scanned page with no text layer, kept as one image


@dataclass
class Redaction:
    page: int


@dataclass
class FrontHeading:
    title: str
    page: int


@dataclass
class Subheading:
    """An unnumbered heading inside a section's content."""
    title: str
    kind: str  # bold (08Subheading Bold) | label (Heading (No TOC))
    page: int = 0


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
        height = page.rect.height
        seen = set()
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                y = line["bbox"][1]
                if PAGE_MARGIN_ZONE * height < y < (1 - PAGE_MARGIN_ZONE) * height:
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
    if not in_margin_zone(line["bbox"], page):
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


def redaction_bars(drawings):
    return [d["rect"] for d in drawings
            if d.get("fill") == BLACK
            and REDACTION_BAR_HEIGHT_RANGE[0] <= d["rect"].height <= REDACTION_BAR_HEIGHT_RANGE[1]
            and d["rect"].width > d["rect"].height]


def find_tables(page, drawings):
    """Tables on the page, read from ruled lines only.

    Cell fills and inline redaction bars are left out: their edges would otherwise split rows
    and columns that the source draws as one cell.
    """
    bars = redaction_bars(drawings)
    if not bars:
        return page.find_tables(strategy="lines_strict").tables
    bar_rects = {tuple(rect) for rect in bars}
    ruling = [d for d in drawings if tuple(d["rect"]) not in bar_rects and not in_margin_zone(d["rect"], page)]
    if not ruling:
        # Redaction bars were the only line art; removing them below leaves artifacts that read as a table.
        return []
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


def table_grid(page, table, lines, drawings):
    """Turn a PyMuPDF table into a grid with explicit row/column spans."""
    xs = sorted({round(c[0], 1) for c in table.cells} | {round(c[2], 1) for c in table.cells})
    ys = sorted({round(c[1], 1) for c in table.cells} | {round(c[3], 1) for c in table.cells})
    xs = merge_close(xs)
    ys = merge_close(ys)
    n_cols, n_rows = len(xs) - 1, len(ys) - 1
    if n_cols < 1 or n_rows < 1:
        return None
    grid = [[None] * n_cols for _ in range(n_rows)]
    for cell in table.cells:
        c0, c1 = nearest(xs, cell[0]), nearest(xs, cell[2])
        r0, r1 = nearest(ys, cell[1]), nearest(ys, cell[3])
        if c1 <= c0 or r1 <= r0 or grid[r0][c0] is not None:
            continue
        cell_lines = [ln for ln in lines if inside(ln[0]["bbox"], cell)]
        grid[r0][c0] = {
            "rowspan": r1 - r0,
            "colspan": c1 - c0,
            "paragraphs": cell_paragraphs(cell_lines, page.number + 1),
            "fill": cell_fill(cell, drawings),
        }
        for r in range(r0, r1):
            for c in range(c0, c1):
                if (r, c) != (r0, c0) and grid[r][c] is None:
                    grid[r][c] = "covered"
    for r in range(n_rows):
        for c in range(n_cols):
            if grid[r][c] is None:
                grid[r][c] = {"rowspan": 1, "colspan": 1, "paragraphs": [], "fill": None}
            elif grid[r][c] == "covered":
                grid[r][c] = None
    widths = [xs[i + 1] - xs[i] for i in range(n_cols)]
    return Table(grid, widths, page.number + 1, page.number + 1, cell_padding(table, lines),
                 table.bbox[3] - table.bbox[1])


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
        if not page_lines and page.get_images():
            scan = Figure(render_clip(page, page.rect), page.rect.width, page_no, full_page=True)
            items.append((page_no, 0, "figure", scan))
            continue
        all_rows = group_rows(lines, page_no)
        if is_toc_page(all_rows):
            # Contents pages are replaced by a TOC field; keep only their rows so headings like
            # "TABLE OF CONTENTS" still switch regions.
            toc_pages.add(page_no)
            items.extend((page_no, row.y0, "row", row) for row in all_rows)
            continue

        drawings = page.get_drawings()
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
            if is_redaction(page, drawings, rect):
                items.append((page_no, rect[1], "redaction", Redaction(page_no)))
            else:
                items.append((page_no, rect[1], "figure", Figure(render_clip(page, rect), rect[2] - rect[0], page_no)))

        free_lines = [ln for ln in lines if not any(inside(ln[0]["bbox"], r) for r in taken)]
        for row in group_rows(free_lines, page_no):
            items.append((page_no, row.y0, "row", row))
    items.sort(key=lambda item: (item[0], item[1]))
    return items, toc_pages


# ---------------------------------------------------------------- structure

def skipped_numbers(previous, number):
    """Section numbers missing between `previous` and `number`, or None if `number` can't follow it.

    An empty list means `number` is the direct successor. When `number` lies under `previous` but
    more than one level deeper, the skipped headings include the levels in between (8.10 then
    8.10.1.3 skips 8.10.1, 8.10.1.1 and 8.10.1.2).
    """
    parts = [int(p) for p in number.split(".")]
    prev = [int(p) for p in previous.split(".")] if previous else []
    parent = parts[:-1]
    if prev and len(parent) > len(prev) and parts[:len(prev)] == prev:
        return skipped_below(prev, parts)
    if len(parent) > len(prev) or prev[:len(parent)] != parent:
        return None
    start = prev[len(parent)] + 1 if len(prev) > len(parent) else 1
    if parts[-1] < start:
        return None
    return [".".join(map(str, parent + [k])) for k in range(start, parts[-1])]


def skipped_below(prev, parts):
    skipped = []
    for depth in range(len(prev) + 1, len(parts) + 1):
        prefix = parts[:depth - 1]
        skipped.extend(".".join(map(str, prefix + [k])) for k in range(1, parts[depth - 1]))
        if depth < len(parts):
            skipped.append(".".join(map(str, parts[:depth])))
    return skipped


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
    elif match and row.is_bold() and "." in match.group(1) and len(row.pieces) == 1:
        number, title, combined = match.group(1), REDACTED_HEADING_TITLE, False
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


def section_sort_key(number):
    return [int(part) for part in number.split(".")]


def bookmark_gap(bookmarks, last_position, number, emitted):
    """Bookmarked headings listed after the last emitted one and before `number` that the text never showed."""
    numbers = [bookmark_number for bookmark_number, _, _ in bookmarks]
    position = numbers.index(number)
    return [bookmarks[index] for index in range(last_position + 1, position) if numbers[index] not in emitted]


def structure(items, toc_pages, body_size, bookmarks=()):
    """Split rows into title, front matter and body.

    `bookmarks` is [(number, title, page)] from the PDF outline. A heading the outline places on
    its own page is accepted without a numbering chain, and bookmarked headings the text lacks
    (under a black box, on a scanned page) become placeholders carrying the bookmark's title.
    """
    doc = Document()
    bookmark_pages = {(number, page) for number, _, page in bookmarks}
    bookmark_titles = {number: title for number, title, _ in reversed(bookmarks)}
    bookmark_numbers = [number for number, _, _ in bookmarks]
    emitted, last_position = set(), -1
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
        if region == "title" and page_no > TITLE_PAGE_LAST:
            flush()
            region = "front"
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
        redacted_title = bool(candidate) and candidate[1] == REDACTED_HEADING_TITLE
        bookmarked = bool(candidate) and (candidate[0], page_no) in bookmark_pages and emphasized \
            and candidate[0] not in emitted
        if candidate and page_no not in toc_pages and x_ok and (bookmarked or (
                skipped is not None
                and not (redacted_title and skipped)
                and (emphasized or (not candidate[2] and "." in candidate[0])
                     or (styled_differently and not skipped and candidate[0].count(".") >= 2))
                and (not skipped or (emphasized and len(skipped) <= MAX_SKIPPED_HEADINGS)))):
            heading_x.setdefault(landscape, payload.x0)
            flush()
            number, title, combined = candidate
            # Long headings wrap onto following rows of the same block, aligned with the title text
            # (or anywhere in the hanging indent when number and title share one run).
            title_x = None if combined or redacted_title else row.pieces[1][0]
            while i < len(items) and items[i][2] == "row" and not redacted_title:
                nxt = items[i][3]
                aligned = (row.x0 - 2 <= nxt.x0 <= row.x0 + WRAPPED_TITLE_MAX_INDENT) if combined \
                    else abs(nxt.x0 - title_x) < WRAPPED_TITLE_X_TOLERANCE
                if nxt.page == row.page and (nxt.block == row.block or not combined) and aligned \
                        and nxt.y0 - row.y1 < row.height \
                        and nxt.starts_bold() == row.is_bold() \
                        and abs(nxt.size() - row.size()) < WRAPPED_TITLE_SIZE_TOLERANCE:
                    title += " " + nxt.text
                    i += 1
                    row = nxt
                else:
                    break
            if region != "body":
                region = "body"
                doc.body.append(PageMark(landscape, page_no))
            missing_headings = {missing: (bookmark_titles.get(missing, MISSING_HEADING_TITLE), page_no)
                                for missing in skipped or [] if missing not in emitted}
            if bookmarked:
                missing_headings.update({gap_number: (gap_title, gap_page) for gap_number, gap_title, gap_page
                                         in bookmark_gap(bookmarks, last_position, number, emitted)})
            for missing in sorted(missing_headings, key=section_sort_key):
                doc.body.append(Heading(missing, *missing_headings[missing]))
                emitted.add(missing)
            doc.body.append(Heading(number, title, page_no))
            emitted.add(number)
            if number in bookmark_numbers:
                last_position = bookmark_numbers.index(number)
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


def is_label_text(text):
    return text.endswith(":") or canonical_label(text) is not None


def value_column_x(rows):
    """Left edge of the value column when the title page is laid out as label | value columns."""
    starts = Counter(round(piece[0]) for row in rows for piece in row.pieces[1:])
    labels = sum(1 for row in rows if len(row.pieces) > 1 and is_label_text(row.first_piece_text()))
    if not starts or labels < MIN_TWO_COLUMN_LABELS:
        return None
    x, count = starts.most_common(1)[0]
    return x if count >= MIN_TWO_COLUMN_LABELS else None


def two_column_fields(rows, value_x):
    """Pair labels in the left column (which may wrap over several rows, ending in ':') with the
    text beside them in the right column."""
    fields = []
    label_open = False  # the last label hasn't reached its closing ':' yet
    sentence_open = False  # the last unlabeled sentence wraps onto the next row
    for row in rows:
        left = " ".join("".join(s.text for s in spans).strip() for x, spans in row.pieces if x < value_x - 5).strip()
        right = " ".join("".join(s.text for s in spans).strip() for x, spans in row.pieces if x >= value_x - 5).strip()
        if right or not left:
            sentence_open = False
        if left and not right and sentence_open:
            fields[-1][1] += " " + left
            sentence_open = not left.endswith(".")
        elif left and not right and (len(left) > MAX_LABEL_LENGTH or MEDICAL_MONITOR_STATEMENT.match(left)
                                     or (COVER_HEADING.match(left) and not label_open)):
            fields.append([None, left])
            label_open = False
            sentence_open = bool(MEDICAL_MONITOR_STATEMENT.match(left)) and not left.endswith(".")
        elif left and label_open and not starts_new_label(fields[-1], left):
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
        if label and not is_label_text(label):
            out.append([None, f"{label} {value}".strip()])
        else:
            out.append([label, value])
    return out


def starts_new_label(open_field, next_text):
    """A wrapped label continues on the next row unless it already has its value or both texts are labels."""
    open_label, open_value = open_field
    if canonical_label(open_label) and canonical_label(next_text):
        return True
    return bool(open_value) and ":" not in next_text and not next_text.startswith("(")


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

    def page_break(self):
        p = self.paragraph(self.styles["body"])
        p.add_run().add_break(docx.enum.text.WD_BREAK.PAGE)

    def set_orientation(self, landscape):
        if landscape == self.landscape or self.landscape_sect is None:
            return
        ending = copy.deepcopy(self.landscape_sect if self.landscape else self.portrait_sect)
        for tag in ("w:titlePg", "w:pgNumType"):  # the template's first section restarts numbering at 1
            for ref in ending.findall(qn(tag)):
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
        self.numbered_heading(heading.title, heading.level)
        if heading.level > len(self.styles["headings"]):
            warnings.append(f"heading {heading.number} deeper than template supports")

    def numbered_heading(self, title, level):
        index = min(level, len(self.styles["headings"])) - 1
        p = self.paragraph(self.styles["headings"][index], text=title)
        if self.heading_numpr:
            p._p.get_or_add_pPr().insert(1, copy.deepcopy(self.heading_numpr[index]))

    def subheading(self, subheading):
        style = self.styles["front_heading"] if subheading.kind == "bold" else self.styles["field_label"]
        self.paragraph(style, text=subheading.title).paragraph_format.keep_with_next = True

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
            caption = self.paragraph(self.styles["caption"], para.spans)
            caption.paragraph_format.keep_with_next = True
            return caption
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
        font_size = table_font_size(table) * scale
        for r, row in enumerate(table.grid):
            keep_with_next = keep_together and r < n_rows - 1
            for c, cell in enumerate(row):
                if cell is None:
                    continue
                target = t.cell(r, c)
                # A full-width cell that also spans rows leaves continuation rows with one full-width
                # vMerge cell, which the M11 validator's docx reader turns into a zero-cell row and crashes on
                rowspan = 1 if cell["colspan"] >= n_cols else cell["rowspan"]
                if rowspan > 1 or cell["colspan"] > 1:
                    target = target.merge(t.cell(r + rowspan - 1, c + cell["colspan"] - 1))
                if cell["fill"]:
                    shade_cell(target, cell["fill"])
                first = True
                for para in cell["paragraphs"]:
                    p = target.paragraphs[0] if first else target.add_paragraph()
                    first = False
                    p.style = self.doc.styles[self.styles["bullet"] if para.kind == "bullet" else self.styles["table_text"]]
                    for s in para.spans:
                        run_size = font_size if s.superscript or not s.size else s.size * scale
                        self.add_run(p, s, size=run_size)
                if first:
                    target.paragraphs[0].style = self.doc.styles[self.styles["table_text"]]
                for p in target.paragraphs:
                    compact_cell_paragraph(p, font_size, keep_with_next)
                    if p.style.name == self.styles["bullet"]:
                        p.paragraph_format.left_indent = Pt(CELL_BULLET_INDENT_PT)
                        p.paragraph_format.first_line_indent = Pt(-CELL_BULLET_INDENT_PT)
        for row in t.rows:
            row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        t.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))

    def simple_table(self, rows):
        """A plain bordered table of text cells; the first row is the header."""
        t = self.doc.add_table(rows=len(rows), cols=len(rows[0]))
        self.body.remove(t._tbl)
        self.final_sect.addprevious(t._tbl)
        t.style = self.doc.styles["Table Grid"]
        for row, texts in zip(t.rows, rows):
            for cell, text in zip(row.cells, texts):
                cell.paragraphs[0].style = self.doc.styles[self.styles["table_text"]]
                cell.paragraphs[0].add_run(text)
        t.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))

    def figure(self, figure):
        width = min(figure.width_pt, self.text_width_pt)
        if figure.full_page:
            image = Image.from_blob(figure.png)
            available_height = self.text_height_pt - SCANNED_PAGE_VERTICAL_ALLOWANCE
            width = min(width, available_height * image.px_width / max(image.px_height, 1))
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
            elif title:
                p.add_run(title)
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


def set_cell_margins(table, horizontal_pt, vertical_pt):
    margins = OxmlElement("w:tblCellMar")
    for side, size in (("top", vertical_pt), ("left", horizontal_pt),
                       ("bottom", vertical_pt), ("right", horizontal_pt)):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:w"), str(round(size * TWIPS_PER_PT)))
        el.set(qn("w:type"), "dxa")
        margins.append(el)
    table._tbl.tblPr.append(margins)


def shade_cell(cell, hex_fill):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    cell._tc.get_or_add_tcPr().append(shd)


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
    """Join a table continued on the next page (same columns, nothing in between but a page break)."""
    out = []
    for item in blocks:
        if isinstance(item, Table):
            previous = [x for x in out if not isinstance(x, PageMark)]
            prev = previous[-1] if previous else None
            between = out[out.index(prev) + 1:] if prev is not None else []
            if isinstance(prev, Table) and len(prev.col_widths) == len(item.col_widths) \
                    and item.page == prev.page + 1 and all(isinstance(x, PageMark) for x in between):
                rows = item.grid
                if rows and same_row_text(rows[0], prev.grid[0]):
                    rows = rows[1:]
                prev.grid.extend(rows)
                prev.page = item.page
                continue
        out.append(item)
    return out


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


def write_block(writer, block):
    """Write one content block; blocks that carry no content (page marks, headings) are ignored."""
    if isinstance(block, Paragraph):
        writer.body_paragraph(block)
    elif isinstance(block, Table):
        writer.table(block)
    elif isinstance(block, Figure):
        writer.figure(block)
    elif isinstance(block, Redaction):
        writer.paragraph(writer.styles["body"], text="[Redacted: CCI]")
    elif isinstance(block, (FrontHeading, Subheading)):
        writer.subheading(Subheading(block.title, getattr(block, "kind", "bold")))


V011_TITLE_ORDER = [
    "title", "acronym", "protocol_number", "version", "date", "amendment_number", "compound", "phase",
    "sponsor", "manufacturer",
]
MISSING_FIELD_VALUE = "[Not found in source protocol]"
REGISTRY_LABEL = re.compile(r"\b(IND|NCT|WHO|EUDAMED|EudraCT|EU CT|CTIS|ISRCTN|EU trial number)\b[\s:#]*(?:number|no\.?)?[\s:#]*([A-Z0-9][\w\-/]*\d[\w\-/]*)", re.IGNORECASE)


def collect_title_values(doc, notes=None):
    """({field key: value}, [(label, value) of fields without a known key]) from the title rows and tables."""
    notes = [] if notes is None else notes
    values, others = {}, []
    pairs = [tuple(pair) for pair in title_fields(doc.title_rows)]
    for table in doc.title_tables:
        for row in table.grid:
            texts = [" ".join(p.text for p in cell["paragraphs"]).strip() for cell in row if cell]
            if len(texts) < 2:
                continue
            label, value = texts[0], " ".join(texts[1:]).strip()
            if not label and pairs and pairs[-1][0]:
                pairs[-1] = (pairs[-1][0], f"{pairs[-1][1]} {value}".strip())
            elif label and not value:
                pairs.append(split_known_label(label))
            elif label:
                pairs.append((label, value))
    for label, value in pairs:
        key = canonical_label(label) if label else None
        shares_line_with_registry_ids = bool(label) and not key and label_after_registry_ids(label) is not None
        if not label and value and canonical_label(value) == "medical_monitor":
            values.setdefault("medical_monitor", value)
        elif not label and COVER_HEADING.match(value):
            continue
        elif shares_line_with_registry_ids:
            # The value also holds the sponsor line printed beside the ids, so it is not a usable compound.
            add_registry_ids(values, label)
        elif label and DATE_LABEL.search(label) and ELECTRONIC_SIGNATURE_VALUE.match(value):
            notes.append(f"title page: {label.rstrip(':')} is only '{value}'; Version Date left as placeholder")
        elif key in ("sponsor", "sponsor_address"):
            values["sponsor"] = join_sponsor(values.get("sponsor"), value)
        elif key == "registry_id":
            values["regulatory_ids"] = "; ".join(x for x in (values.get("regulatory_ids"), f"{label.rstrip(':')}: {value}") if x)
        elif key:
            if value:
                values.setdefault(key, value)
        elif label:
            others.append((label, value))
        elif value:
            others.append((None, value))
    return values, others


def split_known_label(text):
    """(label, value) when a cell holds a known label followed by its value, else (text, '')."""
    words = text.split()
    for count in range(len(words) - 1, 0, -1):
        prefix = " ".join(words[:count])
        if canonical_label(prefix):
            return prefix, " ".join(words[count:])
    return text, ""


def label_after_registry_ids(label):
    """The label left once registry ids are removed from `EU CT: 123 IND Number: 456 Compound:`, or None."""
    if not registry_rows(label):
        return None
    remainder = REGISTRY_LABEL.sub("", label).strip()
    return remainder if canonical_label(remainder) else None


def add_registry_ids(values, text):
    known = values.get("regulatory_ids", "")
    for label, identifier in registry_rows(text):
        if identifier not in known:
            known = "; ".join(x for x in (known, f"{label}: {identifier}") if x)
    values["regulatory_ids"] = known


def join_sponsor(name, address):
    """Name and address of the sponsor; the address often repeats the name."""
    if name and address.startswith(name):
        return address
    return " ".join(part for part in (name, address) if part)


def registry_rows(text):
    return [(match.group(1).upper(), match.group(2)) for match in REGISTRY_LABEL.finditer(text)]


M11_IDENTIFIER_NAMES = {
    "IND": "FDA IND Number", "NCT": "NCT Number", "WHO": "WHO-UTN Number",
    "EU CT": "EU CT Number", "EU TRIAL NUMBER": "EU CT Number", "CTIS": "EU CT Number",
}
OTHER_IDENTIFIER_NAME = "Other Regulatory or Clinical Trial Identifier"
AMENDMENT_STATEMENTS = {
    "none": "This protocol has not been amended.",
    "first": "This is the first protocol amendment.",
    "later": "This protocol has been amended previously. Details of prior amendments are presented in Section 12.3 Prior Protocol Amendment(s).",
}
# Title-page fields M11 requires after the slot fields the template order covers
AFTER_PROTOCOL_NUMBER = ("Original Protocol:", "original_protocol")
AFTER_AMENDMENT_IDENTIFIER = ("Amendment Scope:", "amendment_scope")


def amendment_state(values):
    """'none', 'first' or 'later' from the amendment number the source gives."""
    digits = re.search(r"\d+", values.get("amendment_number", ""))
    if not digits:
        return "none"
    return "first" if int(digits.group()) == 1 else "later"


def write_title_page(writer, values, others, notes):
    """Write the v011 title page: the template's fields in its order, then fields it has no slot for."""
    styles = writer.styles
    labels = CPT_TITLE_LABELS["v011"]
    state = amendment_state(values)
    derived = {
        "original_protocol": "Yes" if state == "none" else "No",
        # Global vs Not Global (a code list) can't be read from a v010 title page
        "amendment_scope": "",
    }
    extra_after = {"protocol_number": AFTER_PROTOCOL_NUMBER, "amendment_number": AFTER_AMENDMENT_IDENTIFIER}

    def field(label, text):
        writer.paragraph(styles["field_label"], text=label)
        if text:
            writer.paragraph(styles["body"], text=text)

    writer.paragraph(styles["page_title"], text="Title Page")
    for key in V011_TITLE_ORDER:
        value = values.get(key)
        if not value:
            notes.append(f"title page: {labels[key]} not found in source")
        # a code-list field can't hold the placeholder
        field(labels[key], value or ("" if key == "phase" else MISSING_FIELD_VALUE))
        if key in extra_after:
            label, derived_key = extra_after[key]
            field(label, derived[derived_key])
    field(labels["regulatory_ids"], "")
    rows = [(M11_IDENTIFIER_NAMES.get(label, OTHER_IDENTIFIER_NAME), identifier)
            for label, identifier in registry_rows(values.get("regulatory_ids", ""))]
    if not rows:
        notes.append("title page: regulatory identifiers not found in source")
        rows = [("", MISSING_FIELD_VALUE)]
    writer.simple_table([("Label", "ID")] + rows)
    field(labels["signatory"], values.get("signatory") or MISSING_FIELD_VALUE)
    field(labels["medical_monitor"], values.get("medical_monitor") or MISSING_FIELD_VALUE)
    field("Sponsor Approval:", values.get("date") or MISSING_FIELD_VALUE)
    field("Amendment Details", AMENDMENT_STATEMENTS[state])
    for label, value in others:
        if label:
            writer.paragraph(styles["field_label"], text=label)
        if value:
            writer.paragraph(styles["body"], text=value)
        notes.append(f"title page: extra field kept at the end: {label or value[:50]}")
    return values.get("protocol_number")


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
        write_block(writer, item)
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
        else:
            write_block(writer, item)
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
