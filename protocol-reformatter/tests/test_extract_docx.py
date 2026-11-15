import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unittest.mock import patch

import docx
import pymupdf
from docx.enum.style import WD_STYLE_TYPE
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import docx_coverage
from converter.extract import docx as reader_module
from converter.extract.docx import PNG_SIGNATURE, load_docx, read_docx
from pdf_to_cpt_docx import (
    Figure, FrontHeading, Heading, Paragraph, Subheading, Table, collect_title_values,
)

SAMPLE = Path(__file__).resolve().parents[1] / "resources" / "example-protocols" / "v010" / "sampleV10inputV2.docx"
CUSTOM_STYLES = (
    "Heading (No TOC)", "Heading 1 (No TOC)", "Heading 2 (non-numbered)", "Heading 3 (No TOC)",
    "CPT_Example", "CPT_Instructional", "01Heading 1", "02Heading 2", "08Subheading Bold",
)
GUIDANCE_TEXT = "Describe the criteria for discontinuation of the participant from study intervention."
PIXEL_SIZE = 4
IMAGE_WIDTH_INCHES = 2
PT_PER_INCH = 72


def new_document() -> docx.document.Document:
    document = docx.Document()
    for name in CUSTOM_STYLES:
        document.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH).base_style = document.styles["Normal"]
    return document


def labeled(document, label: str, value: str, style: str = "Normal"):
    paragraph = document.add_paragraph(style=style)
    paragraph.add_run(label).bold = True
    paragraph.add_run(" " + value)
    return paragraph


def with_body_start(document) -> None:
    document.add_paragraph("Protocol Summary", style="Heading 1")


def pixmap_bytes(fmt: str) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, PIXEL_SIZE, PIXEL_SIZE), False)
    pixmap.clear_with(200)
    return pixmap.tobytes(fmt)


class ExtractDocxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        template = new_document()
        template.add_paragraph(GUIDANCE_TEXT, style="CPT_Instructional")
        self.template = Path(self.temp.name) / "template.docx"
        template.save(self.template)

    def load(self, document):
        path = Path(self.temp.name) / "input.docx"
        document.save(path)
        return read_docx(path, self.template)

    def test_numbers_headings_from_both_style_families(self) -> None:
        document = new_document()
        document.add_paragraph("Introduction", style="Heading 1")
        document.add_paragraph("Background", style="Heading 2")
        document.add_paragraph("2.1 Typed Number", style="Heading 2")
        document.add_paragraph("Design", style="01Heading 1")
        document.add_paragraph("Overall", style="02Heading 2")
        result, notes = self.load(document)
        headings = [(b.number, b.title) for b in result.body if isinstance(b, Heading)]
        self.assertEqual(headings, [("1", "Introduction"), ("1.1", "Background"), ("1.2", "Typed Number"),
                                    ("2", "Design"), ("2.1", "Overall")])
        self.assertTrue(any("typed number 2.1 differs from counted 1.2" in n for n in notes))

    def test_skipped_heading_level_gets_placeholder(self) -> None:
        document = new_document()
        document.add_paragraph("One", style="Heading 1")
        document.add_paragraph("Deep", style="Heading 3")
        result, _ = self.load(document)
        headings = [(b.number, b.title) for b in result.body if isinstance(b, Heading)]
        self.assertEqual([number for number, _ in headings], ["1", "1.1", "1.1.1"])
        self.assertEqual(headings[1][1], "[Heading not present in source PDF]")

    def test_splits_title_front_and_body(self) -> None:
        document = new_document()
        document.add_paragraph("Title Page", style="Heading 1 (No TOC)")
        labeled(document, "Protocol Title:", "A Study of Things", "Heading (No TOC)")
        labeled(document, "Study Phase:", "II", "Heading (No TOC)")
        document.add_paragraph("Protocol Amendment Summary of Changes", style="Heading 2 (non-numbered)")
        document.add_paragraph("Version 2 revised the dose.")
        document.add_paragraph("Table of Contents", style="Heading 3 (No TOC)")
        document.add_paragraph("1 Summary ........ 5")
        document.add_paragraph("List of Abbreviations", style="Heading 3 (No TOC)")
        document.add_paragraph("AE: adverse event")
        with_body_start(document)
        document.add_paragraph("Body text.")
        result, _ = self.load(document)

        values, _ = collect_title_values(result)
        self.assertEqual(values["title"], "A Study of Things")
        self.assertEqual(values["phase"], "II")
        self.assertEqual([type(b).__name__ for b in result.front],
                         ["FrontHeading", "Paragraph", "FrontHeading", "Paragraph"])
        self.assertEqual([b.title for b in result.front if isinstance(b, FrontHeading)],
                         ["Protocol Amendment Summary of Changes", "List of Abbreviations"])
        self.assertNotIn("Summary ........", " ".join(b.text for b in result.front if isinstance(b, Paragraph)))
        self.assertEqual(result.body[-1].text, "Body text.")

    def test_template_guidance_is_dropped_and_author_text_is_kept(self) -> None:
        document = new_document()
        with_body_start(document)
        document.add_paragraph("<start of example text>", style="Normal")
        document.add_paragraph(GUIDANCE_TEXT, style="CPT_Instructional")
        document.add_paragraph(GUIDANCE_TEXT.replace("Describe", "describe") + " now", style="CPT_Instructional")
        document.add_paragraph("In order to describe the proportion of somatic cases we classify each person.",
                               style="CPT_Instructional")
        document.add_paragraph("Not applicable to the present study.", style="CPT_Example")
        document.add_paragraph(GUIDANCE_TEXT, style="CPT_Example")  # template wording an author adopted
        document.add_paragraph("[Insert text]", style="Normal")
        document.add_paragraph("<end of example text>", style="Normal")
        result, notes = self.load(document)
        texts = [b.text for b in result.body if isinstance(b, Paragraph)]
        self.assertEqual(texts, ["In order to describe the proportion of somatic cases we classify each person.",
                                 "Not applicable to the present study.", GUIDANCE_TEXT])
        self.assertTrue(any("dropped 2 template guidance paragraphs" in n for n in notes))
        self.assertTrue(any("dropped template guidance: 'Describe the criteria" in n for n in notes))
        self.assertTrue(any("dropped 2 start/end markers" in n for n in notes))
        self.assertTrue(any("dropped 1 placeholder-only" in n for n in notes))

    def test_template_text_in_table_cells_and_short_labels_is_kept(self) -> None:
        document = new_document()
        with_body_start(document)
        document.add_paragraph("Population:", style="CPT_Example")
        table = document.add_table(rows=1, cols=1)
        table.cell(0, 0).paragraphs[0].text = GUIDANCE_TEXT
        table.cell(0, 0).paragraphs[0].style = "CPT_Instructional"
        result, _ = self.load(document)
        self.assertIn("Population:", [b.text for b in result.body if isinstance(b, Paragraph)])
        cell = next(b for b in result.body if isinstance(b, Table)).grid[0][0]
        self.assertEqual([p.text for p in cell["paragraphs"]], [GUIDANCE_TEXT])


    def add_xml_paragraph(self, document, inner: str, style: str | None = None, mark_deleted: bool = False):
        props = ""
        if style or mark_deleted:
            props = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + \
                ('<w:rPr><w:del w:id="9" w:author="a"/></w:rPr>' if mark_deleted else "") + "</w:pPr>"
        element = parse_xml(f'<w:p {nsdecls("w")}>{props}{inner}</w:p>')
        document.element.body.insert(len(document.element.body) - 1, element)
        return element

    def test_deleted_paragraph_mark_joins_next_paragraph(self) -> None:
        document = new_document()
        with_body_start(document)
        self.add_xml_paragraph(document, '<w:r><w:t xml:space="preserve">Hello </w:t></w:r>', "ListBullet", True)
        self.add_xml_paragraph(document, '<w:r><w:t>world</w:t></w:r>')
        self.add_xml_paragraph(document, '<w:del w:id="1" w:author="a"><w:r><w:delText>gone entirely</w:delText></w:r></w:del>',
                               mark_deleted=True)
        self.add_xml_paragraph(document, '<w:r><w:t>kept after removal</w:t></w:r>', "ListBullet")
        result, notes = self.load(document)
        paragraphs = [(b.kind, b.text) for b in result.body if isinstance(b, Paragraph)]
        self.assertEqual(paragraphs, [("bullet", "Hello world"), ("bullet", "kept after removal")])
        self.assertTrue(any("1 paragraphs joined to the next one, 1 fully deleted" in n for n in notes))

    def test_field_codes_are_hidden_and_results_kept(self) -> None:
        document = new_document()
        with_body_start(document)

        def field(code: str, result: str) -> str:
            return ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                    f'<w:r><w:instrText xml:space="preserve"> {code} </w:instrText></w:r>'
                    '<w:r><w:fldChar w:fldCharType="separate"/></w:r>' + result +
                    '<w:r><w:fldChar w:fldCharType="end"/></w:r>')

        nested = field("ADDIN EN.CITE &lt;EndNote&gt;", '<w:r><w:t>(Smith 2020)</w:t></w:r>')
        self.add_xml_paragraph(document, '<w:r><w:t xml:space="preserve">Prior work </w:t></w:r>' +
                               field("HYPERLINK \\l x", nested) + '<w:r><w:t>.</w:t></w:r>')
        self.add_xml_paragraph(document, '<w:r><w:t xml:space="preserve">See </w:t></w:r>'
                               '<w:fldSimple w:instr=" REF x "><w:r><w:t>Table 1</w:t></w:r></w:fldSimple>')
        result, _ = self.load(document)
        texts = [b.text for b in result.body if isinstance(b, Paragraph)]
        self.assertEqual(texts, ["Prior work (Smith 2020).", "See Table 1"])

    def add_notes_part(self, document, kind: str, notes_xml: str) -> None:
        part_name = f"/word/{kind}s.xml"
        blob = f'<w:{kind}s {nsdecls("w")}>{notes_xml}</w:{kind}s>'.encode()
        content_type = f"application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}s+xml"
        part = Part(PackURI(part_name), content_type, blob, document.part.package)
        document.part.relate_to(part, f"http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}s")

    def test_footnotes_and_endnotes_follow_their_block(self) -> None:
        document = new_document()
        with_body_start(document)
        self.add_notes_part(document, "footnote",
                            '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
                            '<w:footnote w:id="1"><w:p><w:r><w:footnoteRef/></w:r><w:r><w:t xml:space="preserve"> Per label.</w:t></w:r></w:p></w:footnote>')
        self.add_notes_part(document, "endnote",
                            '<w:endnote w:id="1"><w:p><w:r><w:endnoteRef/></w:r><w:r><w:t xml:space="preserve"> See Appendix.</w:t></w:r></w:p></w:endnote>')
        self.add_xml_paragraph(document, '<w:r><w:t>Dose text</w:t></w:r><w:r><w:footnoteReference w:id="1"/></w:r>')
        self.add_xml_paragraph(document, '<w:r><w:t>Other text</w:t></w:r><w:r><w:endnoteReference w:id="1"/></w:r>')
        result, notes = self.load(document)
        paragraphs = [b for b in result.body if isinstance(b, Paragraph)]
        self.assertEqual([p.text for p in paragraphs], ["Dose text1", "1 Per label.", "Other texti", "i See Appendix."])
        self.assertTrue(paragraphs[0].spans[-1].superscript)
        self.assertTrue(paragraphs[1].spans[0].superscript)
        self.assertTrue(any("1 footnotes read" in n for n in notes))

    def test_text_box_is_read_once_after_its_anchor(self) -> None:
        document = new_document()
        with_body_start(document)
        box = "<w:txbxContent><w:p><w:r><w:t>Inside the box</w:t></w:r></w:p></w:txbxContent>"
        self.add_xml_paragraph(
            document,
            '<w:r><w:t>Anchor text</w:t></w:r><w:r>'
            '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
            'xmlns:v="urn:schemas-microsoft-com:vml">'
            f'<mc:Choice Requires="wps">{box}</mc:Choice><mc:Fallback><w:pict><v:textbox>{box}</v:textbox></w:pict></mc:Fallback>'
            '</mc:AlternateContent></w:r>')
        result, notes = self.load(document)
        self.assertEqual([b.text for b in result.body if isinstance(b, Paragraph)], ["Anchor text", "Inside the box"])
        self.assertTrue(any("1 text boxes read" in n for n in notes))

    def test_image_in_table_cell_gets_marker_and_follows_table(self) -> None:
        document = new_document()
        with_body_start(document)
        table = document.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "Schema"
        table.cell(0, 1).paragraphs[0].add_run().add_picture(io.BytesIO(pixmap_bytes("png")))
        result, notes = self.load(document)
        kinds = [type(b).__name__ for b in result.body if isinstance(b, (Table, Figure))]
        self.assertEqual(kinds, ["Table", "Figure"])
        cell = next(b for b in result.body if isinstance(b, Table)).grid[0][1]
        self.assertEqual([p.text for p in cell["paragraphs"]], ["[Figure]"])
        self.assertTrue(any("images inside table cells" in n for n in notes))

    def test_symbols_breaks_math_and_nested_tables(self) -> None:
        document = new_document()
        with_body_start(document)
        math = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
        self.add_xml_paragraph(
            document,
            '<w:r><w:t xml:space="preserve">Age </w:t><w:sym w:font="Symbol" w:char="F0B3"/><w:t>18</w:t><w:br/>'
            '<w:t>re</w:t><w:softHyphen/><w:t>test</w:t><w:noBreakHyphen/><w:t>x</w:t></w:r>'
            f'<m:oMath {math}><m:r><m:t>y</m:t></m:r><m:sSup><m:e><m:r><m:t>z</m:t></m:r></m:e>'
            '<m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup></m:oMath>')
        table = document.add_table(rows=1, cols=1)
        cell = table.cell(0, 0)
        cell.paragraphs[0].text = "outer"
        nested = cell.add_table(rows=1, cols=2)
        nested.cell(0, 0).text = "inner one"
        nested.cell(0, 1).text = "inner two"
        result, notes = self.load(document)
        text = next(b for b in result.body if isinstance(b, Paragraph))
        self.assertEqual(text.text, "Age ≥18 retest-xyz2")
        self.assertTrue(text.spans[-1].superscript)
        cell_texts = [p.text for p in next(b for b in result.body if isinstance(b, Table)).grid[0][0]["paragraphs"]]
        self.assertEqual(cell_texts, ["outer", "inner one", "inner two"])
        self.assertTrue(any("1 equations flattened" in n for n in notes))

    def test_unconvertible_image_bytes_are_kept(self) -> None:
        document = new_document()
        with_body_start(document)
        document.add_picture(io.BytesIO(pixmap_bytes("png")))
        part = next(rel.target_part for rel in document.part.rels.values() if rel.reltype.endswith("/image"))
        part._blob = b"emf bytes"
        result, _ = self.load(document)
        self.assertEqual([data for _, data in result.unconverted_images], [b"emf bytes"])

    def test_notes_are_attached_to_the_document(self) -> None:
        document = new_document()
        with_body_start(document)
        path = Path(self.temp.name) / "notes.docx"
        document.save(path)
        loaded = load_docx(path, self.template)
        self.assertTrue(any("tracked changes" in note for note in loaded.notes))

    def test_tracked_changes_keep_insertions_and_drop_deletions(self) -> None:
        document = new_document()
        with_body_start(document)
        paragraph = document.add_paragraph("Dose is ")
        paragraph._p.append(parse_xml(
            f'<w:ins {nsdecls("w")} w:id="1" w:author="a"><w:r><w:t>20 mg</w:t></w:r></w:ins>'))
        paragraph._p.append(parse_xml(
            f'<w:del {nsdecls("w")} w:id="2" w:author="a"><w:r><w:delText> 10 mg</w:delText></w:r></w:del>'))
        paragraph.add_run(" daily.")
        result, notes = self.load(document)
        self.assertEqual([b.text for b in result.body if isinstance(b, Paragraph)], ["Dose is 20 mg daily."])
        self.assertTrue(any("1 insertions kept, 1 deletions dropped" in n for n in notes))

    def test_table_resolves_grid_and_vertical_merges(self) -> None:
        document = new_document()
        with_body_start(document)
        table = document.add_table(rows=3, cols=3)
        table.cell(0, 0).merge(table.cell(0, 1))
        table.cell(1, 2).merge(table.cell(2, 2))
        for r, row in enumerate(table.rows):
            for c, cell in enumerate(row.cells):
                cell.text = f"r{r}c{c}"
        result, _ = self.load(document)
        grid = next(b for b in result.body if isinstance(b, Table)).grid
        self.assertEqual((len(grid), len(grid[0])), (3, 3))
        self.assertEqual((grid[0][0]["colspan"], grid[0][0]["rowspan"]), (2, 1))
        self.assertIsNone(grid[0][1])
        self.assertEqual((grid[1][2]["rowspan"], grid[1][2]["colspan"]), (2, 1))
        self.assertIsNone(grid[2][2])
        self.assertEqual(grid[2][0]["paragraphs"][0].text, "r2c0")
        self.assertEqual(len(next(b for b in result.body if isinstance(b, Table)).col_widths), 3)

    def test_cell_shading_and_nested_paragraphs(self) -> None:
        document = new_document()
        with_body_start(document)
        table = document.add_table(rows=1, cols=1)
        cell = table.cell(0, 0)
        cell.text = "first"
        cell.add_paragraph("second")
        cell._tc.get_or_add_tcPr().append(parse_xml(f'<w:shd {nsdecls("w")} w:val="clear" w:fill="d9e2f3"/>'))
        result, _ = self.load(document)
        cell_data = next(b for b in result.body if isinstance(b, Table)).grid[0][0]
        self.assertEqual([p.text for p in cell_data["paragraphs"]], ["first", "second"])
        self.assertEqual(cell_data["fill"], "D9E2F3")

    def test_figures_are_extracted_as_png(self) -> None:
        document = new_document()
        with_body_start(document)
        for fmt in ("png", "jpg"):
            document.add_picture(io.BytesIO(pixmap_bytes(fmt)), width=docx.shared.Inches(IMAGE_WIDTH_INCHES))
        result, _ = self.load(document)
        figures = [b for b in result.body if isinstance(b, Figure)]
        self.assertEqual(len(figures), 2)
        for figure in figures:
            self.assertTrue(figure.png.startswith(PNG_SIGNATURE))
            self.assertAlmostEqual(figure.width_pt, IMAGE_WIDTH_INCHES * PT_PER_INCH, places=1)

    def test_unconvertible_image_becomes_placeholder_with_note(self) -> None:
        document = new_document()
        with_body_start(document)
        document.add_picture(io.BytesIO(pixmap_bytes("png")))
        part = next(rel.target_part for rel in document.part.rels.values() if rel.reltype.endswith("/image"))
        part._blob = b"not an image"
        result, notes = self.load(document)
        self.assertFalse(any(isinstance(b, Figure) for b in result.body))
        self.assertTrue(any(isinstance(b, Paragraph) and "not converted" in b.text for b in result.body))
        self.assertTrue(any("could not be converted" in n for n in notes))

    def test_bullets_and_numbered_lists(self) -> None:
        document = new_document()
        with_body_start(document)
        document.add_paragraph("first", style="List Bullet")
        document.add_paragraph("second", style="List Bullet")
        document.add_paragraph("Figure 1. Study flow", style="Normal")
        result, _ = self.load(document)
        kinds = [(b.kind, b.text) for b in result.body if isinstance(b, Paragraph)]
        self.assertEqual(kinds, [("bullet", "first"), ("bullet", "second"), ("caption", "Figure 1. Study flow")])

    def test_unnumbered_subheading_in_body(self) -> None:
        document = new_document()
        with_body_start(document)
        document.add_paragraph("Extra heading", style="08Subheading Bold")
        result, _ = self.load(document)
        self.assertIn(("bold", "Extra heading"), [(b.kind, b.title) for b in result.body if isinstance(b, Subheading)])

    def test_section_orientation_marks(self) -> None:
        document = new_document()
        with_body_start(document)
        document.add_paragraph("portrait text")
        section = document.add_section()
        section.orientation = docx.enum.section.WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = section.page_height, section.page_width
        document.add_paragraph("landscape text")
        result, _ = self.load(document)
        marks = [(b.landscape, b.page) for b in result.body if type(b).__name__ == "PageMark"]
        self.assertEqual(marks, [(False, 1), (True, 2)])


@unittest.skipUnless(SAMPLE.exists(), "sample v010 docx not available")
class SampleDocxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document, cls.notes = read_docx(SAMPLE)
        cls.source = docx.Document(str(SAMPLE))

    def test_headings_match_source_count_and_nesting(self) -> None:
        headings = [b for b in self.document.body if isinstance(b, Heading)]
        self.assertEqual(len(headings), 134)
        self.assertEqual([h.number for h in headings[:3]], ["1", "1.1", "1.2"])
        self.assertIn("9.3.2.4", [h.number for h in headings])

    def test_every_source_table_is_read_with_its_shape(self) -> None:
        tables = [b for part in (self.document.title_tables, self.document.front, self.document.body)
                  for b in part if isinstance(b, Table)]
        body_tables = [b for b in self.document.body if isinstance(b, Table)]
        self.assertEqual(len(body_tables) + 1, len(self.source.tables) - 2)  # title page tables are folded
        soa = next(t for t in body_tables if len(t.col_widths) == 13)
        source_soa = next(t for t in self.source.tables if len(t.columns) == 13)
        self.assertEqual(len(soa.grid), len(source_soa.rows))
        self.assertTrue(any(cell and cell["colspan"] > 1 for row in soa.grid for cell in row))
        self.assertTrue(any(cell and cell["rowspan"] > 1 for row in soa.grid for cell in row))
        self.assertTrue(all(len(row) == 13 for row in soa.grid))
        self.assertGreater(len(tables), len(body_tables))

    def test_figures_and_front_matter(self) -> None:
        self.assertEqual(sum(isinstance(b, Figure) for b in self.document.body), 3)
        titles = [b.title for b in self.document.front if isinstance(b, FrontHeading)]
        self.assertEqual(titles, ["Protocol Amendment Summary of Changes", "List of Abbreviations and Definitions of Terms"])

    def test_title_values_and_tracked_changes(self) -> None:
        values, _ = collect_title_values(self.document)
        self.assertEqual(values["version"], "2.0")
        self.assertEqual(values["acronym"], "REMUS")
        self.assertNotIn("26-April-2026", values["signatory"])
        self.assertTrue(any("420 insertions kept, 348 deletions dropped" in n for n in self.notes))

    def test_load_docx_returns_document(self) -> None:
        self.assertEqual(len(load_docx(SAMPLE).body), len(self.document.body))

    def test_coverage_check_finds_nothing_unexplained(self) -> None:
        report = docx_coverage.check(SAMPLE)
        self.assertEqual(report.unexplained_total, 0, report.unexplained + report.cell_misses + report.title_misses)


class CoverageToolTests(unittest.TestCase):
    """The coverage check must notice when the reader loses text."""

    LOST = "This paragraph is lost by a broken reader"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        template = new_document()
        template.add_paragraph("Guidance only text for authors to read", style="CPT_Instructional")
        self.template = Path(self.temp.name) / "template.docx"
        template.save(self.template)
        document = new_document()
        with_body_start(document)
        document.add_paragraph("Kept paragraph with enough words to count.")
        document.add_paragraph(self.LOST)
        document.add_paragraph("Yes")
        table = document.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "Visit"
        table.cell(0, 1).text = "X"
        self.source = Path(self.temp.name) / "source.docx"
        document.save(self.source)

    def test_intact_reader_has_no_unexplained_text(self) -> None:
        self.assertEqual(docx_coverage.check(self.source, self.template).unexplained_total, 0)

    def test_lost_paragraph_is_reported(self) -> None:
        original = reader_module.Reader.paragraph

        def lossy(reader, element, in_cell=False):
            paragraph = original(reader, element, in_cell)
            return None if paragraph and paragraph.text == self.LOST else paragraph

        with patch.object(reader_module.Reader, "paragraph", lossy):
            report = docx_coverage.check(self.source, self.template)
        self.assertIn(self.LOST, report.unexplained)

    def test_lost_short_cell_text_is_reported_by_position(self) -> None:
        original = reader_module.Reader.paragraph

        def lossy(reader, element, in_cell=False):
            paragraph = original(reader, element, in_cell)
            return None if paragraph and in_cell and paragraph.text == "X" else paragraph

        with patch.object(reader_module.Reader, "paragraph", lossy):
            report = docx_coverage.check(self.source, self.template)
        self.assertEqual(len(report.cell_misses), 1)
        self.assertIn("(0,1)", report.cell_misses[0])


if __name__ == "__main__":
    unittest.main()
