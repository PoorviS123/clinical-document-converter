import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from converter.extract.pdf import numbered_bookmarks, same_heading_title, source_checks
from pdf_to_cpt_docx import (
    REDACTED_HEADING_TITLE, Document, Figure, Heading, PageMark, Row, Span, find_tables, skipped_numbers, structure,
)

BODY_SIZE = 12.0
HEADING_X = 72.0
TITLE_X = 126.0


def bold(text):
    return Span(text, True, False, False, BODY_SIZE)


def plain(text):
    return Span(text, False, False, False, BODY_SIZE)


def row(page, top, pieces, block=0):
    return Row(page, pieces[0][0], top, top + 14, block, pieces)


def heading_row(page, top, number, title, block=0):
    return row(page, top, [(HEADING_X, [bold(number)]), (TITLE_X, [bold(title)])], block)


def body_row(page, top, text):
    return row(page, top, [(HEADING_X, [plain(text)])], block=99)


def items_for(rows):
    pages = sorted({page for page, _ in rows})
    items = [(page, -1, "page", PageMark(False, page)) for page in pages]
    items.extend((page, payload.y0, "row", payload) for page, payload in rows)
    return sorted(items, key=lambda item: (item[0], item[1]))


def body_headings(document):
    return [(block.number, block.title, block.page) for block in document.body if isinstance(block, Heading)]


class SkippedNumberTests(unittest.TestCase):
    def test_levels_between_a_parent_and_a_deeper_heading_are_skipped(self):
        self.assertEqual(skipped_numbers("8.10", "8.10.1.3"), ["8.10.1", "8.10.1.1", "8.10.1.2"])
        self.assertEqual(skipped_numbers("8.10", "8.10.2"), ["8.10.1"])
        self.assertEqual(skipped_numbers("8.10", "8.10.1"), [])

    def test_unrelated_numbers_still_cannot_follow(self):
        self.assertIsNone(skipped_numbers("8.9.1", "8.10.1.3"))
        self.assertIsNone(skipped_numbers(None, "1.2.3"))


class StructureTests(unittest.TestCase):
    def test_redacted_title_heading_is_kept(self):
        rows = [(1, heading_row(1, 100, "1", "Introduction")),
                (1, heading_row(1, 130, "1.1", "Background")),
                (1, row(1, 160, [(HEADING_X, [bold("1.2")])])),
                (1, body_row(1, 200, "Body text follows the redacted heading."))]
        document = structure(items_for(rows), set(), BODY_SIZE)
        self.assertEqual(body_headings(document)[-1], ("1.2", REDACTED_HEADING_TITLE, 1))

    def test_unbolded_bare_number_is_not_a_heading(self):
        rows = [(1, heading_row(1, 100, "1", "Introduction")),
                (1, row(1, 130, [(HEADING_X, [plain("1.1")])]))]
        document = structure(items_for(rows), set(), BODY_SIZE)
        self.assertEqual([number for number, _, _ in body_headings(document)], ["1"])

    def test_wrapped_title_continues_in_another_block_with_indent_and_plain_reference(self):
        rows = [(1, heading_row(1, 100, "1", "Introduction")),
                (1, heading_row(1, 130, "1.1", "Criteria for Delaying", block=3)),
                (1, row(1, 145, [(TITLE_X + 7, [bold("Reporting to Sponsor (Section "), plain("8.4"), bold(")")])], block=4)),
                (1, body_row(1, 200, "Body text."))]
        document = structure(items_for(rows), set(), BODY_SIZE)
        self.assertEqual(body_headings(document)[-1][1], "Criteria for Delaying Reporting to Sponsor (Section 8.4)")

    def test_bookmarks_supply_headings_the_text_lacks(self):
        bookmarks = [("1", "Protocol Summary", 1), ("1.1", "Synopsis", 1), ("2", "Introduction", 3),
                     ("2.1", "Background", 3)]
        rows = [(3, heading_row(3, 100, "2.1", "Background"))]
        document = structure(items_for(rows), set(), BODY_SIZE, bookmarks)
        self.assertEqual(body_headings(document), [("1", "Protocol Summary", 1), ("1.1", "Synopsis", 1),
                                                   ("2", "Introduction", 3), ("2.1", "Background", 3)])


class SkippedLevelStructureTests(unittest.TestCase):
    def test_heading_two_levels_below_the_last_one_gets_placeholders_for_the_levels_between(self):
        bookmarks = [("1", "Summary", 1), ("1.1", "Synopsis", 1), ("1.1.1.3", "Urgent Care Data", 1)]
        rows = [(1, heading_row(1, 100, "1", "Summary")), (1, heading_row(1, 130, "1.1", "Synopsis")),
                (1, heading_row(1, 160, "1.1.1.3", "Urgent Care Data"))]
        document = structure(items_for(rows), set(), BODY_SIZE, bookmarks)
        self.assertEqual([number for number, _, _ in body_headings(document)],
                         ["1", "1.1", "1.1.1", "1.1.1.1", "1.1.1.2", "1.1.1.3"])


class FullyRedactedHeadingTests(unittest.TestCase):
    def test_bookmarked_headings_with_no_text_at_all_are_placed_between_their_neighbours(self):
        bookmarks = [("6.6", "Dose Modification", 2), ("6.6.1", "CCI", 2), ("6.6.2", "CCI", 2),
                     ("6.7", "Continued Access", 2)]
        rows = [(2, heading_row(2, 130, "6.6", "Dose Modification")), (2, heading_row(2, 400, "6.7", "Continued Access"))]
        document = structure(items_for(rows), set(), BODY_SIZE, bookmarks)
        self.assertEqual(body_headings(document),
                         [("6.6", "Dose Modification", 2), ("6.6.1", "CCI", 2), ("6.6.2", "CCI", 2),
                          ("6.7", "Continued Access", 2)])


class BookmarkCheckTests(unittest.TestCase):
    def make_pdf(self, directory, bookmarks):
        path = Path(directory) / "source.pdf"
        with pymupdf.open() as pdf:
            for _ in range(3):
                pdf.new_page()
            pdf.set_toc(bookmarks)
            pdf.save(path)
        return path

    def test_bookmarks_on_scanned_pages_are_listed_not_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_pdf(directory, [[1, "1. Summary", 1], [1, "2. Design", 2], [2, "2.1. Arms", 3]])
            scan = Figure(b"", 100.0, 1, True)
            document = Document(body=[scan, Heading("2", "Design", 2)])
            checks = source_checks(path, document)
            self.assertEqual(checks["bookmark_mismatches"], ["bookmark 2.1 Arms p3: detected not found"])
            self.assertEqual(checks["bookmarks_on_scanned_pages"], ["bookmark 1 Summary p1: on scanned page, not checkable"])

    def test_redacted_titles_match_each_other(self):
        self.assertTrue(same_heading_title("[REDACTED]", "CCI"))
        self.assertTrue(same_heading_title("CCI", "[REDACTED]"))
        self.assertFalse(same_heading_title("[REDACTED]", "Synopsis"))

    def test_numbered_bookmarks_skip_unnumbered_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_pdf(directory, [[1, "Cover", 1], [1, "1.2. CCI", 2], [1, "3 Methods", 3]])
            self.assertEqual(numbered_bookmarks(path), [("1.2", "CCI", 2), ("3", "Methods", 3)])


class TableFinderTests(unittest.TestCase):
    REDACTED_HEADING_PAGE = (Path(__file__).resolve().parents[1] / "resources" / "example-protocols" / "v010"
                             / "2025-523807-31-00_Protocol.pdf", 29)

    def test_redaction_bars_alone_are_not_a_table(self):
        """Page 30 of this PDF is redaction bars around the heading 4.3.2; they once read as one table."""
        path, page_index = self.REDACTED_HEADING_PAGE
        if not path.exists():
            self.skipTest("example protocol not available")
        with pymupdf.open(path) as pdf:
            page = pdf[page_index]
            self.assertEqual(find_tables(page, page.get_drawings()), [])


if __name__ == "__main__":
    unittest.main()
