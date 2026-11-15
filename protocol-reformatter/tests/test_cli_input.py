import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from converter import cli
from converter.title_page import enrich_title


class LoadSourceTests(unittest.TestCase):
    def test_pdf_uses_pdf_loader_case_insensitive(self) -> None:
        with patch.object(cli, "load_pdf", return_value="pdf-doc") as pdf, \
                patch.object(cli, "load_docx") as docx:
            self.assertEqual(cli.load_source(Path("a.PDF")), "pdf-doc")
        pdf.assert_called_once_with(Path("a.PDF"))
        docx.assert_not_called()

    def test_docx_uses_docx_loader_case_insensitive(self) -> None:
        with patch.object(cli, "load_pdf") as pdf, patch.object(cli, "load_docx", return_value="docx-doc") as docx:
            self.assertEqual(cli.load_source(Path("a.DocX")), "docx-doc")
        docx.assert_called_once_with(Path("a.DocX"))
        pdf.assert_not_called()

    def test_other_extension_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported input type"):
            cli.load_source(Path("a.txt"))

    def test_main_rejects_unsupported_extension(self) -> None:
        with patch.object(sys, "argv", ["converter", "a.doc", "--out-dir", "out"]):
            with self.assertRaisesRegex(SystemExit, r"unsupported input type '\.doc'"):
                cli.main()


class EnrichTitleTests(unittest.TestCase):
    def test_docx_skips_pdf_reading(self) -> None:
        values = {"version": "Amendment 2 EU-3"}
        notes: list[str] = []
        with patch("converter.title_page.pymupdf.open") as opened:
            enrich_title(Path("missing.docx"), values, [], notes)
        opened.assert_not_called()
        self.assertEqual(values["amendment_number"], "Amendment 2 EU-3")
        self.assertEqual(notes, [])


class EnrichTitlePdfTests(unittest.TestCase):
    PAGE_ONE = [
        "Date: 5 May 2025",
        "Protocol Amendment 2 Final",
        "CLINICAL STUDY PROTOCOL",
        "Regeneron Pharmaceuticals, Inc.",
    ]
    PAGE_TWO = ["Version number: 3.0"]

    def enrich(self, values: dict[str, str]) -> list[str]:
        notes: list[str] = []
        with patch("converter.title_page.first_two_pages", return_value=(self.PAGE_ONE, self.PAGE_TWO)) as pages:
            enrich_title(Path("protocol.PDF"), values, [], notes)
        pages.assert_called_once_with(Path("protocol.PDF"))
        return notes

    def test_pdf_reads_page_text(self) -> None:
        values: dict[str, str] = {}
        notes = self.enrich(values)
        self.assertEqual(values["date"], "5 May 2025")
        self.assertEqual(values["version"], "3.0")
        self.assertEqual(values["amendment_number"], "2")
        self.assertEqual(values["sponsor"], "Regeneron Pharmaceuticals, Inc.")
        self.assertTrue(any("Regeneron" in note for note in notes))
        self.assertTrue(any("first-page labels" in note for note in notes))

    def test_existing_sponsor_kept(self) -> None:
        values = {"sponsor": "Other Sponsor"}
        self.enrich(values)
        self.assertEqual(values["sponsor"], "Other Sponsor")


if __name__ == "__main__":
    unittest.main()
