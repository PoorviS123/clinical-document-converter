"""Loss-free XHTML for extracted blocks; data attributes retain PDF layout and runs."""

import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

from pdf_to_cpt_docx import (
    Figure, FrontHeading, Heading, PageMark, Paragraph, Redaction, Span, Subheading, Table,
)


def tag(element):
    return element.tag.rsplit("}", 1)[-1]


def attributes(**values):
    return {"data-" + key.replace("_", "-"): json.dumps(value) for key, value in values.items()}


def value(element, key, default):
    return json.loads(element.get("data-" + key.replace("_", "-"), json.dumps(default)))


def paragraph_element(block, name="p"):
    element = ET.Element(name, attributes(kind=block.kind, level=block.level, page=block.page, first_x=block.first_x))
    if block.kind == "caption":
        element.set("class", "caption")
    for span in block.spans:
        run = ET.SubElement(element, "span", attributes(size=span.size))
        for enabled, name in ((span.bold, "strong"), (span.italic, "em"), (span.superscript, "sup")):
            if enabled:
                run = ET.SubElement(run, name)
        run.text = span.text
    return element


def blocks_to_xhtml(blocks, assets):
    assets = Path(assets)
    root = ET.Element("div")
    lists = []
    for block in blocks:
        if isinstance(block, Paragraph) and block.kind == "bullet":
            depth = max(block.level, 0) + 1
            while len(lists) > depth:
                lists.pop()
            while len(lists) < depth:
                parent = root if not lists else lists[-1][-1] if len(lists[-1]) else ET.SubElement(lists[-1], "li", {"data-container": "true"})
                lists.append(ET.SubElement(parent, "ul"))
            lists[-1].append(paragraph_element(block, "li"))
            continue
        lists.clear()
        if isinstance(block, Paragraph):
            root.append(paragraph_element(block))
        elif isinstance(block, Table):
            table = ET.SubElement(root, "table", attributes(
                col_widths=block.col_widths, page=block.page, first_page=block.first_page,
                padding=block.padding, height=block.height, columns=len(block.col_widths)))
            columns = ET.SubElement(table, "colgroup")
            for width in block.col_widths:
                ET.SubElement(columns, "col", {"style": f"width:{width}pt"})
            for row_index, row in enumerate(block.grid):
                tr = ET.SubElement(table, "tr")
                for column, cell in enumerate(row):
                    if cell is None:
                        continue
                    metadata = {key: item for key, item in cell.items() if key != "paragraphs"}
                    td = ET.SubElement(tr, "th" if row_index == 0 else "td", {
                        "rowspan": str(cell["rowspan"]), "colspan": str(cell["colspan"]),
                        **attributes(column=column, cell=metadata),
                    })
                    if cell.get("fill"):
                        td.set("style", f"background-color:#{cell['fill']}")
                    td.extend(paragraph_element(paragraph) for paragraph in cell["paragraphs"])
        elif isinstance(block, Figure):
            assets.mkdir(parents=True, exist_ok=True)
            filename = "fig-" + hashlib.sha256(block.png).hexdigest() + ".png"
            (assets / filename).write_bytes(block.png)
            ET.SubElement(root, "img", {"src": f"{assets.name}/{filename}", "alt": "Scanned source page" if block.full_page else "Source figure", **attributes(
                width_pt=block.width_pt, page=block.page, full_page=block.full_page)})
        elif isinstance(block, Redaction):
            ET.SubElement(root, "span", {"class": "redaction", **attributes(page=block.page)}).text = "CCI"
        elif isinstance(block, (Subheading, FrontHeading, Heading)):
            element = ET.SubElement(root, "p", {"class": "subheading", **attributes(
                block=type(block).__name__, kind=getattr(block, "kind", "bold"),
                number=getattr(block, "number", ""), page=block.page)})
            element.text = block.title
        elif isinstance(block, PageMark):
            ET.SubElement(root, "span", {"class": "page-mark", **attributes(page=block.page, landscape=block.landscape)})
        else:
            raise TypeError(f"Unsupported block: {type(block).__name__}")
    return "".join(ET.tostring(child, encoding="unicode") for child in root)


def read_paragraph(element, page):
    spans = []

    def read_runs(node, bold=False, italic=False, superscript=False, size=0):
        bold = bold or tag(node) == "strong"
        italic = italic or tag(node) == "em"
        superscript = superscript or tag(node) == "sup"
        size = value(node, "size", size)
        if node.text is not None:
            spans.append(Span(node.text, bold, italic, superscript, size))
        elif not len(node) and tag(node) in ("span", "strong", "em", "sup"):
            spans.append(Span("", bold, italic, superscript, size))
        for child in node:
            read_runs(child, bold, italic, superscript, size)
            if child.tail:
                spans.append(Span(child.tail, bold, italic, superscript, size))

    read_runs(element)
    return Paragraph(spans, value(element, "kind", "bullet" if tag(element) == "li" else "body"),
                     value(element, "level", 0), value(element, "page", page), value(element, "first_x", 0))


def xhtml_to_blocks(xhtml, assets, page=0):
    assets = Path(assets).resolve()
    root = ET.fromstring(f"<div>{xhtml}</div>")
    blocks = []

    def decode(element):
        name = tag(element)
        source_page = value(element, "page", page)
        kind = element.get("class", "")
        if name in ("div", "ul", "ol"):
            for child in element:
                decode(child)
        elif name == "li":
            nested = [child for child in element if tag(child) in ("ul", "ol")]
            for child in nested:
                element.remove(child)
            if element.get("data-container") != "true":
                blocks.append(read_paragraph(element, page))
            for child in nested:
                decode(child)
        elif name == "p" and kind == "subheading":
            title = "".join(element.itertext())
            block_type = value(element, "block", "Subheading")
            if block_type == "FrontHeading":
                blocks.append(FrontHeading(title, source_page))
            elif block_type == "Heading":
                blocks.append(Heading(value(element, "number", ""), title, source_page))
            else:
                blocks.append(Subheading(title, value(element, "kind", "bold"), source_page))
        elif name == "p":
            blocks.append(read_paragraph(element, page))
        elif name == "table":
            rows = [row for row in element.iter() if tag(row) == "tr"]
            grid = [[None] * value(element, "columns", 0) for row in rows]
            for row_index, row in enumerate(rows):
                for cell in row:
                    metadata = value(cell, "cell", {})
                    metadata["paragraphs"] = [read_paragraph(paragraph, page) for paragraph in cell]
                    grid[row_index][value(cell, "column", 0)] = metadata
            blocks.append(Table(grid, value(element, "col_widths", []), source_page,
                                value(element, "first_page", source_page), tuple(value(element, "padding", [0, 0])),
                                value(element, "height", 0)))
        elif name == "img":
            source = (assets.parent / element.attrib["src"]).resolve()
            if not source.is_relative_to(assets):
                raise ValueError(f"Image outside asset directory: {source}")
            blocks.append(Figure(source.read_bytes(), value(element, "width_pt", 0), source_page,
                                 value(element, "full_page", False)))
        elif kind == "redaction":
            blocks.append(Redaction(source_page))
        elif kind == "page-mark":
            blocks.append(PageMark(value(element, "landscape", False), source_page))
        else:
            raise ValueError(f"Unsupported XHTML element: {name}")

    decode(root)
    return blocks
