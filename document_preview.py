"""Small, dependency-light DOCX-to-HTML preview for the local demo.

This intentionally renders document content (paragraphs, basic run formatting,
headings, and tables), not pixel-perfect Word pagination. Original DOCX files
remain available through the download routes.
"""
from __future__ import annotations

from html import escape
from pathlib import Path
import re

from docx import Document
from docx.oxml.ns import qn
from docx.oxml.text.paragraph import CT_P
from docx.oxml.table import CT_Tbl
from docx.text.paragraph import Paragraph
from docx.table import Table


def _render_run(run) -> str:
    text = escape(run.text).replace("\n", "<br>").replace("\t", "&emsp;")
    if not text:
        return ""

    styles = []
    if run.bold:
        styles.append("font-weight:700")
    if run.italic:
        styles.append("font-style:italic")
    if run.underline:
        styles.append("text-decoration:underline")
    if run.font.highlight_color is not None:
        styles.append("background:#fff19a;padding:0 2px")
    if run.font.color and run.font.color.rgb:
        rgb = str(run.font.color.rgb)
        if re.fullmatch(r"[0-9A-Fa-f]{6}", rgb):
            styles.append(f"color:#{rgb}")
    if run.font.size:
        size = max(6, min(float(run.font.size.pt), 48))
        styles.append(f"font-size:{size:g}pt")

    if styles:
        return f'<span style="{";".join(styles)}">{text}</span>'
    return text


def _render_paragraph(paragraph) -> str:
    content = "".join(_render_run(run) for run in paragraph.runs)
    if not content:
        content = "&nbsp;"

    style_name = (paragraph.style.name or "") if paragraph.style else ""
    if style_name.startswith("Heading"):
        match = re.search(r"([1-6])", style_name)
        level = int(match.group(1)) if match else 2
        tag = f"h{level}"
    elif style_name == "Title":
        tag = "h1"
    elif style_name == "Subtitle":
        tag = "p"
    else:
        tag = "p"

    alignment = paragraph.alignment
    align_css = {1: "center", 2: "right", 3: "justify"}.get(alignment, "left")
    return f'<{tag} style="text-align:{align_css}">{content}</{tag}>'


def _render_table(table) -> str:
    rows = []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            paragraphs = "".join(_render_paragraph(p) for p in cell.paragraphs)
            cells.append(f"<td>{paragraphs or '&nbsp;'}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return '<div class="table-wrap"><table>' + "".join(rows) + "</table></div>"


def render_docx_preview(path: str | Path) -> str:
    """Return a safe HTML fragment representing a DOCX's readable content."""
    document = Document(str(path))
    pieces = []
    body = document.element.body
    for child in body.iterchildren():
        if isinstance(child, CT_P):
            pieces.append(_render_paragraph(Paragraph(child, document)))
        elif isinstance(child, CT_Tbl):
            pieces.append(_render_table(Table(child, document)))

    if not pieces:
        pieces.append("<p>This document has no previewable body content.</p>")
    return '<div class="docx-preview-content">' + "".join(pieces) + "</div>"
