import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cross_references import CrossReferenceReport, rewrite_heading_titles, rewrite_paragraph, rewrite_title_values
from pdf_to_cpt_docx import Paragraph, Span
from restructure import add_extra_node, OutNode, Section


def paragraph(text):
    return Paragraph([Span(text, False, False, False, 11)], page=7)


class ContainingSectionTests(unittest.TestCase):
    def test_reference_to_a_section_the_source_lacks_points_at_its_parent(self):
        number_map = {"8.3.1": "9.4.1", "8.3.1.1": "9.4.1.1"}
        report = CrossReferenceReport()
        target = paragraph("Section 8.3.1.1.2 Minor Bleeding (deleted) and Section 8.3.1.2.")
        rewrite_paragraph(target, number_map, set(), report)
        self.assertEqual(target.text, "Section 9.4.1.1 Minor Bleeding (deleted) and Section 9.4.1.")
        self.assertEqual(report.unresolved, [])
        self.assertTrue(any("containing section 8.3.1.1" in entry for entry in report.review))

    def test_reference_with_no_existing_ancestor_stays_unresolved(self):
        report = CrossReferenceReport()
        target = paragraph("See Section 12.4.1.")
        rewrite_paragraph(target, {"1": "1"}, set(), report)
        self.assertEqual(target.text, "See Section 12.4.1.")
        self.assertEqual(len(report.unresolved), 1)

    def test_title_page_text_is_rewritten(self):
        report = CrossReferenceReport()
        rewritten = rewrite_title_values([("Section # and Name", "Section 9.4 Analyses"), (None, "")],
                                         {"9.4": "10.5"}, set(), report)
        self.assertEqual(rewritten, [("Section # and Name", "Section 10.5 Analyses"), (None, "")])


class HeadingTitleTests(unittest.TestCase):
    def test_reference_inside_a_heading_title_is_rewritten(self):
        node = OutNode(4, "Other Events Reporting to Sponsor (Section 8.4.7.1)", number="9.2.1.2")
        rewrite_heading_titles([node], {"8.4": "9.2"}, set(), CrossReferenceReport())
        self.assertEqual(node.title, "Other Events Reporting to Sponsor (Section 9.2)")


class AppendixLabelTests(unittest.TestCase):
    def test_source_appendix_number_is_dropped_from_extra_heading_titles(self):
        parent = OutNode(1, "Appendix: Supporting Details")
        for title, expected in [("APPENDIX 13 DEFINITION OF ANAPHYLAXIS", "DEFINITION OF ANAPHYLAXIS"),
                                ("Appendix 14: List of Vaccines", "List of Vaccines"),
                                ("Appendix of forms", "Appendix of forms")]:
            node = add_extra_node(parent, Section("10.13", title, 1, [], []))
            self.assertEqual(node.title, expected)


if __name__ == "__main__":
    unittest.main()
