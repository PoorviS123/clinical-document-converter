import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docx.oxml.ns import qn

from converter.cli import DEFAULT_TEMPLATE
from converter.title_page import finish_title
from pdf_to_cpt_docx import (
    PageMark, Row, Span, Table, Writer, canonical_label, collect_title_values, structure, title_fields, write_title_page,
)

BODY_SIZE = 12.0
LABEL_X = 72.0
VALUE_X = 250.0
ROW_HEIGHT = 14


def plain_row(page, top, pieces):
    spans = [(x, [Span(text, False, False, False, BODY_SIZE)]) for x, text in pieces]
    return Row(page, pieces[0][0], top, top + ROW_HEIGHT, 0, spans)


def collect_from_pairs(pairs):
    """collect_title_values over title-page [label, value] pairs, bypassing PDF row layout."""
    document = SimpleNamespace(title_rows=[], title_tables=[])
    notes = []
    with patch("pdf_to_cpt_docx.title_fields", return_value=pairs):
        values, others = collect_title_values(document, notes)
    return values, others, notes


class CombinedLabelTests(unittest.TestCase):
    def test_registry_ids_and_compound_on_one_unlabeled_line_leave_no_stray_field(self):
        values, others, _ = collect_from_pairs([
            ["EU CT: 2025-524392-23-00 IND Number: 105379 Compound:", "Regeneron Pharmaceuticals, Inc. Dupilumab (REGN668)"],
            ["Compound:", "Dupilumab (REGN668)"],
        ])
        self.assertEqual(values["regulatory_ids"], "EU CT: 2025-524392-23-00; IND: 105379")
        self.assertEqual(values["compound"], "Dupilumab (REGN668)")
        self.assertEqual(others, [])


class MedicalMonitorTests(unittest.TestCase):
    def test_regeneron_and_sanofi_labels_map_to_medical_monitor(self):
        for label in ("Study Medical Director/Medical Monitor:", "Medical /Study Director:", "Medical/Study Director:",
                      "Monitoring team’s representative name and contact information"):
            self.assertEqual(canonical_label(label), "medical_monitor", label)

    def test_value_is_kept_and_no_extra_field_remains(self):
        values, others, _ = collect_from_pairs([
            ["Study Medical Director/Medical Monitor:", "Regeneron Pharmaceuticals, Inc. 777 Old Saw Mill River Road"]])
        self.assertEqual(values["medical_monitor"], "Regeneron Pharmaceuticals, Inc. 777 Old Saw Mill River Road")
        self.assertEqual(others, [])

    def test_statement_without_a_label_is_the_medical_monitor_value(self):
        statement = "Medical monitor name and contact information will be provided separately."
        values, _, _ = collect_from_pairs([[None, statement]])
        self.assertEqual(values["medical_monitor"], statement)


class DateOfIssueTests(unittest.TestCase):
    def test_electronic_signature_placeholder_is_not_a_date(self):
        values, others, notes = collect_from_pairs([
            ["Date of Issue:", "See appended electronic signature page"],
            ["Amendment 1 EU-1 Date of Issue:", "See appended electronic signature page"],
            ["Original Date of Issue:", "21 November 2025"]])
        self.assertNotIn("date", values)
        self.assertEqual(others, [("Original Date of Issue:", "21 November 2025")])
        self.assertEqual(len(notes), 2)

    def test_printed_date_of_issue_is_the_version_date(self):
        values, _, _ = collect_from_pairs([["Date of Issue:", "19 Feb 2026"]])
        self.assertEqual(values["date"], "19 Feb 2026")


class SponsorTests(unittest.TestCase):
    def test_address_repeating_the_name_is_not_doubled(self):
        values, _, _ = collect_from_pairs([
            ["Sponsor Name:", "Bayer AG"], ["Legal Registered Address:", "Bayer AG, 51368 Leverkusen, Germany"]])
        self.assertEqual(values["sponsor"], "Bayer AG, 51368 Leverkusen, Germany")


class FinishTitleTests(unittest.TestCase):
    def test_extras_repeating_mapped_values_and_cover_headings_are_dropped(self):
        values = {"short_title": "A short study title", "primary_intervention": "Drug X"}
        others = [(None, "Title Page"), (None, "A short study title"), (None, "Based on TMF-1 Protocol v5.0."),
                  ("Clinical Scientist:", "A short study title")]
        notes = []
        finish_title(values, others, notes)
        self.assertEqual(others, [(None, "Based on TMF-1 Protocol v5.0."), ("Clinical Scientist:", "A short study title")])
        self.assertEqual(values["compound"], "Drug X")
        self.assertNotIn("primary_intervention", values)

    def test_primary_intervention_does_not_replace_a_compound(self):
        values = {"compound": "GSK1 / Drug", "primary_intervention": "Drug"}
        finish_title(values, [], [])
        self.assertEqual(values["compound"], "GSK1 / Drug")


class TwoColumnLayoutTests(unittest.TestCase):
    def test_labels_without_colons_pair_with_their_values_and_statement_stays_whole(self):
        rows = [
            plain_row(1, 100, [(LABEL_X, "Primary study intervention(s)"), (VALUE_X, "Drug X")]),
            plain_row(1, 120, [(LABEL_X, "Other study intervention(s)"), (VALUE_X, "Control")]),
            plain_row(1, 140, [(LABEL_X, "Study identifier"), (VALUE_X, "306300")]),
            plain_row(1, 160, [(LABEL_X, "Sponsor signatory"), (VALUE_X, "Patricia Mendez,MD")]),
            plain_row(1, 175, [(VALUE_X, "Clinical Development Lead")]),
            plain_row(1, 200, [(LABEL_X, "Medical monitor name and contact information can be found in the local study")]),
            plain_row(1, 215, [(LABEL_X, "contact information document.")]),
            plain_row(1, 230, [(LABEL_X, "Based on TMF-14732712 Protocol v5.0.")]),
        ]
        fields = title_fields(rows)
        self.assertIn(["Study identifier", "306300"], fields)
        self.assertIn(["Sponsor signatory", "Patricia Mendez,MD Clinical Development Lead"], fields)
        self.assertIn([None, "Other study intervention(s) Control"], fields)
        self.assertIn([None, "Medical monitor name and contact information can be found in the local study "
                             "contact information document."], fields)

    def test_wrapped_labels_with_a_value_still_merge(self):
        rows = [
            plain_row(1, 100, [(LABEL_X, "Protocol number:"), (VALUE_X, "A1")]),
            plain_row(1, 120, [(LABEL_X, "Amendment number:"), (VALUE_X, "02")]),
            plain_row(1, 140, [(LABEL_X, "Compound number"), (VALUE_X, "SAR1")]),
            plain_row(1, 155, [(LABEL_X, "(INN/Trademark):"), (VALUE_X, "x/y")]),
        ]
        self.assertIn(["Compound number (INN/Trademark):", "SAR1 x/y"], title_fields(rows))

    def test_monitoring_team_label_does_not_swallow_the_next_label(self):
        rows = [
            plain_row(1, 100, [(LABEL_X, "Protocol number:"), (VALUE_X, "A1")]),
            plain_row(1, 120, [(LABEL_X, "Amendment number:"), (VALUE_X, "02")]),
            plain_row(1, 130, [(LABEL_X, "Study phase:"), (VALUE_X, "Phase 2")]),
            plain_row(1, 140, [(LABEL_X, "Monitoring team’s representative name and contact information")]),
            plain_row(1, 160, [(LABEL_X, "Regulatory agency identifier number(s):")]),
        ]
        labels = [label for label, _ in title_fields(rows)]
        self.assertIn("Monitoring team’s representative name and contact information", labels)
        self.assertIn("Regulatory agency identifier number(s):", labels)


class RegeneronLayoutTests(unittest.TestCase):
    def test_unlabeled_registry_lines_do_not_become_a_stray_field(self):
        rows = [
            plain_row(1, 100, [(LABEL_X, "EU CT: 2025-524392-23-00"), (VALUE_X + 60, "Regeneron Pharmaceuticals, Inc.")]),
            plain_row(1, 115, [(LABEL_X, "IND Number: 105379")]),
            plain_row(1, 130, [(LABEL_X, "Compound:"), (VALUE_X, "Dupilumab (REGN668)")]),
            plain_row(1, 145, [(LABEL_X, "Study Name/Acronym:"), (VALUE_X, "MISSION")]),
            plain_row(1, 160, [(LABEL_X, "Date of Issue:"), (VALUE_X, "See appended electronic signature page")]),
            plain_row(1, 175, [(LABEL_X, "Study Medical Director/Medical Monitor:"), (VALUE_X, "Tarrytown, NY 10591")]),
        ]
        document = SimpleNamespace(title_rows=rows, title_tables=[])
        values, others = collect_title_values(document, [])
        self.assertEqual(others, [])
        self.assertEqual(values["regulatory_ids"], "EU CT: 2025-524392-23-00; IND: 105379")
        self.assertEqual(values["medical_monitor"], "Tarrytown, NY 10591")
        self.assertNotIn("date", values)


class TitlePageBoundaryTests(unittest.TestCase):
    def test_text_after_the_first_page_is_front_matter_not_title_fields(self):
        items = [(1, -1, "page", PageMark(False, 1)), (2, -1, "page", PageMark(False, 2)),
                 (1, 0, "row", plain_row(1, 100, [(LABEL_X, "Protocol number:"), (VALUE_X, "A1")])),
                 (2, 0, "row", plain_row(2, 100, [(LABEL_X, "Investigator Agreement text on page two.")]))]
        document = structure(items, set(), BODY_SIZE)
        self.assertEqual(len(document.title_rows), 1)
        self.assertEqual(len(document.front), 1)


class WrittenTitlePageTests(unittest.TestCase):
    def test_identifier_table_is_followed_by_the_next_label_not_an_empty_paragraph(self):
        writer = Writer(str(DEFAULT_TEMPLATE), "v011")
        write_title_page(writer, {"regulatory_ids": "EU CT: 2025-524392-23-00; IND: 105379"}, [], [])
        body = list(writer.body)
        table_index = next(index for index, element in enumerate(body) if element.tag == qn("w:tbl"))
        next_text = "".join(node.text or "" for node in body[table_index + 1].iter(qn("w:t")))
        self.assertEqual(next_text, "Sponsor Signatory:")
        label_text = "".join(node.text or "" for node in body[table_index - 1].iter(qn("w:t")))
        self.assertEqual(label_text, "Regulatory or Clinical Trial Identifier(s):")


class M11TitlePageTests(unittest.TestCase):
    def written_texts(self, values):
        writer = Writer(str(DEFAULT_TEMPLATE), "v011")
        write_title_page(writer, values, [], [])
        return ["".join(node.text or "" for node in element.iter(qn("w:t"))) for element in writer.body]

    def test_identifier_table_uses_m11_names(self):
        texts = self.written_texts({"regulatory_ids": "EU CT: 2025-524392-23-00; IND: 105379"})
        table_text = texts[texts.index("Regulatory or Clinical Trial Identifier(s):") + 1]
        self.assertIn("EU CT Number", table_text)
        self.assertIn("FDA IND Number", table_text)

    def test_original_protocol_without_an_amendment_number(self):
        texts = self.written_texts({"protocol_number": "A1"})
        self.assertEqual(texts[texts.index("Original Protocol:") + 1], "Yes")
        self.assertEqual(texts[texts.index("Amendment Details") + 1], "This protocol has not been amended.")

    def test_first_and_later_amendments(self):
        first = self.written_texts({"amendment_number": "01"})
        self.assertEqual(first[first.index("Original Protocol:") + 1], "No")
        self.assertEqual(first[first.index("Amendment Details") + 1], "This is the first protocol amendment.")
        later = self.written_texts({"amendment_number": "Amendment 3"})
        self.assertTrue(later[later.index("Amendment Details") + 1].startswith("This protocol has been amended previously."))

    def test_missing_phase_is_left_blank_not_a_placeholder(self):
        texts = self.written_texts({})
        self.assertEqual(texts[texts.index("Trial Phase:") + 1], "Sponsor Name and Address:")


class FullWidthMergeTests(unittest.TestCase):
    def test_full_width_cell_is_not_merged_down_into_continuation_rows(self):
        paragraph = SimpleNamespace(spans=[Span("Wide", False, False, False, 10)], kind="body")
        wide = {"rowspan": 3, "colspan": 2, "paragraphs": [paragraph], "fill": None}
        grid = [[wide, None], [None, None], [None, None]]
        writer = Writer(str(DEFAULT_TEMPLATE), "v011")
        writer.table(Table(grid, [100.0, 100.0], 1, 1, (3, 2), 100.0))
        table = next(element for element in writer.body if element.tag == qn("w:tbl"))
        self.assertEqual(table.xpath(".//w:vMerge"), [])


if __name__ == "__main__":
    unittest.main()
