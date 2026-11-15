import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdf_to_cpt_docx import Heading, Paragraph, Span, Table
from special_sections import split_objectives, table_provenance


def cell(text, colspan=1, rowspan=1):
    return {"rowspan": rowspan, "colspan": colspan, "fill": None,
            "paragraphs": [Paragraph([Span(text, True, False, False, 10)], page=4)]}


class ObjectivesTests(unittest.TestCase):
    def test_all_three_objective_parts_keep_source_provenance(self):
        table = Table([[cell("Objectives"), cell("Endpoints")],
                       [cell("Primary", colspan=2), None], [cell("First objective"), cell("First endpoint")],
                       [cell("Secondary", colspan=2), None], [cell("Second objective"), cell("Second endpoint")],
                       [cell("Exploratory", colspan=2), None], [cell("Third objective"), cell("Third endpoint")]],
                      [200, 200], 4, 4, (5, 2), 300)
        result = split_objectives([table])
        self.assertTrue(result.split)
        self.assertEqual([len(result.primary), len(result.secondary), len(result.exploratory)], [1, 1, 1])
        pieces = result.primary + result.secondary + result.exploratory
        self.assertEqual([len(piece.grid) for piece in pieces], [3, 3, 3])
        self.assertEqual(table_provenance([Heading("3", "Objectives", 4), table], pieces)["outputs"], [[0], [0], [0]])

    def test_rowspan_prevents_unsafe_split(self):
        table = Table([[cell("Objectives", rowspan=2), cell("Endpoints")],
                       [None, cell("Secondary")]], [200, 200], 4, 4, (5, 2), 100)
        result = split_objectives([table])
        self.assertFalse(result.split)
        self.assertEqual(result.primary, [table])


if __name__ == "__main__":
    unittest.main()
