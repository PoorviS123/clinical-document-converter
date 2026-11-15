import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from block_xhtml import blocks_to_xhtml, xhtml_to_blocks
from pdf_to_cpt_docx import Figure, FrontHeading, Heading, PageMark, Paragraph, Redaction, Span, Subheading, Table


class CodecTests(unittest.TestCase):
    def test_round_trip_all_blocks(self):
        paragraph = Paragraph([Span(" A & <B> ", True, True, True, 10.5),
                               Span("tail", False, False, False, 0)], "caption", 2, 9, 41.25)
        cell = {"rowspan": 2, "colspan": 1, "paragraphs": [paragraph], "fill": "ABCDEF"}
        blocks = [paragraph, Paragraph([Span("bullet", True, False, False, 11)], "bullet", 2, 9, 20),
                  Paragraph([Span("child", False, True, False, 11)], "bullet", 3, 9, 32),
                  Paragraph([Span("sibling", False, False, True, 11)], "bullet", 0, 9, 8),
                  Table([[cell, {**cell, "rowspan": 1}], [None, {**cell, "rowspan": 1}]],
                        [123.5, 88.25], 10, 9, (3.5, 2), 420.5),
                  Figure(b"image bytes", 300.25, 11, True), Redaction(12),
                  Subheading("Label", "label", 13), FrontHeading("Front", 2), Heading("1.1", "Body", 4),
                  PageMark(True, 9)]
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory) / "example.usdm.assets"
            self.assertEqual(blocks, xhtml_to_blocks(blocks_to_xhtml(blocks, assets), assets))

    def test_empty_runs(self):
        blocks = [Paragraph([]), Paragraph([], "bullet"),
                  Paragraph([Span("", True, True, True, 9), Span("text", False, False, False, 12)])]
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory) / "assets"
            self.assertEqual(blocks, xhtml_to_blocks(blocks_to_xhtml(blocks, assets), assets))

    def test_asset_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                xhtml_to_blocks('<img src="../outside.png"/>', Path(directory) / "assets")


if __name__ == "__main__":
    unittest.main()
