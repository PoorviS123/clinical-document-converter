"""Split the v010 synopsis (1.1) and objectives (3) sections into their v011 parts."""

import copy
import re
from dataclasses import dataclass

from cross_references import replace_text
from pdf_to_cpt_docx import Heading, Paragraph, Subheading, Table, split_label

DROPPED = "dropped"
OBJECTIVES = "objectives"
DESIGN = "design"

# Synopsis field labels, matched against the lowercased label without its colon.
SYNOPSIS_LABELS = [
    (DROPPED, r"(protocol |full |study )?title"),
    (DROPPED, r"(brief|short) title"),
    (DROPPED, r"regulatory (agency )?identifier numbers?|regulatory .*identifiers?.*|registry identifiers?"),
    (DROPPED, r"pa?ediatric investigation(al)? plan( number)?"),
    (DROPPED, r"(study )?rationale"),
    (OBJECTIVES, r"objectives?,? (and )?endpoints?(,? and estimands?)?"),
    (DESIGN, r"overall design( synopsis| and synopsis)?"),
    (DESIGN, r"brief summary"),
    (DESIGN, r"health measurement/outcome"),
    (DESIGN, r"condition/disease"),
    (DESIGN, r"number of participants"),
    (DESIGN, r"study population"),
    (DESIGN, r"intervention groups( and duration)?"),
    (DESIGN, r"study arms and duration"),
    (DESIGN, r"study interventions?( and intervention forms)?"),
    (DESIGN, r"study duration"),
    (DESIGN, r"(independent )?data monitoring( committees?)?(/other committees?)?|(steering|adjudication) committees?"),
    (DESIGN, r"statistical (considerations|analysis|methods)"),
]
DROPPED_SNIPPET_LENGTH = 80

PRIMARY = "primary"
SECONDARY = "secondary"
EXPLORATORY = "exploratory"
DIVIDER_WORDS = {
    "primary", "key", "secondary", "tertiary", "exploratory", "other", "objective", "objectives",
    "endpoint", "endpoints", "estimand", "estimands", "for", "and", "efficacy", "safety",
    "pre-specified", "prespecified",
}
MAX_DIVIDER_WORDS = 8


@dataclass
class SynopsisSplit:
    objectives: list
    design: list
    dropped: list


@dataclass
class ObjectivesSplit:
    preamble: list
    primary: list
    secondary: list
    exploratory: list
    split: bool


# ---------------------------------------------------------------- synopsis

def split_synopsis(blocks):
    """Route each synopsis field to 1.1.1 or 1.1.2, or drop it.

    A field runs from its label to the next known label. Unknown bold lines (often the rows of
    an objectives table, such as "Primary") stay in the current field.
    """
    result = SynopsisSplit([], [], [])
    targets = {OBJECTIVES: result.objectives, DESIGN: result.design}
    current = DESIGN
    dropped_label = None
    dropped_text = []
    for block in blocks:
        label = synopsis_label(block)
        if label is None:
            if current == DROPPED:
                dropped_text.append(block_text(block))
            else:
                targets[current].append(block)
            continue
        if current == DROPPED:
            result.dropped.append(dropped_report(dropped_label, dropped_text))
        current, title, remainder = label
        if current == DROPPED:
            dropped_label = (title, block.page)
            dropped_text = [remainder.text] if remainder else []
            continue
        if current == DESIGN:
            result.design.append(Subheading(title, "label", block.page))
        if remainder:
            targets[current].append(remainder)
    if current == DROPPED:
        result.dropped.append(dropped_report(dropped_label, dropped_text))
    return result


def synopsis_label(block):
    """(bucket, label title, remainder) for a paragraph that starts with a known synopsis label.

    The remainder is a copy of the paragraph with the label removed, or None when the paragraph
    is only the label.
    """
    if not isinstance(block, Paragraph):
        return None
    label, _ = split_label(block.spans)
    if not label:
        return None
    title = label.rstrip(":").strip()
    name = re.sub(r"\s+", " ", title.lower())
    bucket = next((bucket for bucket, pattern in SYNOPSIS_LABELS if re.fullmatch(pattern, name)), None)
    if bucket is None:
        return None
    text = "".join(span.text for span in block.spans)
    label_pattern = r"\s*" + r"\s+".join(map(re.escape, title.split())) + r"\s*:?\s*"
    label_end = re.match(label_pattern, text, re.IGNORECASE).end()
    if not text[label_end:].strip():
        return bucket, title, None
    remainder = copy.deepcopy(block)
    replace_text(remainder.spans, 0, label_end, "")
    return bucket, title, remainder


def dropped_report(label, texts):
    title, page = label
    content = " ".join(text for text in texts if text)
    if len(content) > DROPPED_SNIPPET_LENGTH:
        content = content[:DROPPED_SNIPPET_LENGTH].rstrip() + "…"
    return f"p{page}: synopsis field '{title}' dropped: {content or '(empty)'}"


def block_text(block):
    if isinstance(block, Paragraph):
        return block.text
    if isinstance(block, Table):
        return " | ".join(cell_text(cell) for row in block.grid for cell in row if cell and cell_text(cell))
    return f"[{type(block).__name__}]"


def cell_text(cell):
    return " ".join(paragraph.text for paragraph in cell["paragraphs"]).strip()


# ---------------------------------------------------------------- objectives

def split_objectives(blocks):
    """Split section 3 at its Primary / Secondary / Exploratory dividers.

    Dividers are kept as the first block (or table row) of their part. Text before the first
    divider is the preamble.
    """
    parts = {None: [], PRIMARY: [], SECONDARY: [], EXPLORATORY: []}
    current = None
    for block in blocks:
        if isinstance(block, Paragraph) and is_bold(block) and divider_bucket(block.text):
            current = divider_bucket(block.text)
            parts[current].append(block)
        elif isinstance(block, Table):
            for bucket, piece in table_pieces(block):
                current = bucket or current
                parts[current].append(piece)
        else:
            parts[current].append(block)
    if not any(parts[bucket] for bucket in (PRIMARY, SECONDARY, EXPLORATORY)):
        return ObjectivesSplit([], list(blocks), [], [], False)
    return ObjectivesSplit(parts[None], parts[PRIMARY], parts[SECONDARY], parts[EXPLORATORY], True)


def divider_bucket(text):
    """The part a divider line such as "Key Secondary" or "Tertiary/Exploratory" starts, else None."""
    words = re.findall(r"[a-z]+(?:-[a-z]+)*", text.lower().replace("(s)", ""))
    if not words or len(words) > MAX_DIVIDER_WORDS or not set(words) <= DIVIDER_WORDS:
        return None
    if "tertiary" in words or "exploratory" in words:
        return EXPLORATORY
    if "secondary" in words:
        return SECONDARY
    if "primary" in words:
        return PRIMARY
    if "other" in words:
        return EXPLORATORY
    return None


def is_bold(paragraph):
    spans = [span for span in paragraph.spans if span.text.strip()]
    return bool(spans) and all(span.bold for span in spans)


def table_pieces(table):
    """[(bucket, Table)]: the table cut before each divider row, with the header rows repeated.

    The bucket is None for rows before the first divider. A table without dividers comes back
    whole.
    """
    grid = table.grid
    dividers = [index for index, row in enumerate(grid) if row_bucket(row) and can_cut(grid, index)]
    if not dividers:
        return [(None, table)]
    header_end = 0 if dividers[0] == 0 else next(index for index in range(1, len(grid) + 1)
                                                 if can_cut(grid, index))
    header = grid[:header_end]
    segments = [(None, header_end, dividers[0])] if header_end < dividers[0] else []
    boundaries = dividers + [len(grid)]
    segments += [(row_bucket(grid[start]), start, end) for start, end in zip(boundaries, boundaries[1:])]
    pieces = []
    for bucket, start, end in segments:
        header_rows = copy.deepcopy(header) if pieces else header
        pieces.append((bucket, table_piece(table, header_rows, grid[start:end])))
    return pieces


def row_bucket(row):
    texts = [cell_text(cell) for cell in row if cell and cell_text(cell)]
    return divider_bucket(" ".join(texts)) if texts else None


def can_cut(grid, index):
    """True when no merged cell from the rows above reaches row `index`."""
    return all(cell is None or row_index + cell["rowspan"] <= index
               for row_index, row in enumerate(grid[:index]) for cell in row)


def table_piece(table, header_rows, body_rows):
    pages = [paragraph.page for row in body_rows for cell in row if cell for paragraph in cell["paragraphs"]]
    first_page = min(pages, default=table.first_page)
    last_page = max(pages, default=table.page)
    rows = header_rows + body_rows
    height = table.height * len(rows) / len(table.grid)
    return Table(rows, list(table.col_widths), last_page, first_page, table.padding, height)


def table_provenance(source_blocks, output_blocks):
    sources = []
    heading = "front"
    for block in source_blocks:
        if isinstance(block, Heading):
            heading = block.number
        if isinstance(block, Table):
            sources.append((block, heading, {id(paragraph) for row in block.grid for cell in row if cell
                                             for paragraph in cell["paragraphs"]}))
    outputs = []
    for block in output_blocks:
        if not isinstance(block, Table):
            continue
        paragraphs = {id(paragraph) for row in block.grid for cell in row if cell for paragraph in cell["paragraphs"]}
        outputs.append([index for index, (source, heading, source_paragraphs) in enumerate(sources)
                        if source is block or paragraphs & source_paragraphs])
    return {"sources": [{"page": source.first_page, "section": heading} for source, heading, paragraphs in sources],
            "outputs": outputs}
