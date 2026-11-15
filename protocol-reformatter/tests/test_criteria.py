import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from converter.criteria import criterion_text, split_criteria


def block(text, index):
    source = {"section_id": "v10:5.1", "found_number": "5.1", "found_heading": "Inclusion", "match": "exact",
              "source_indices": [index], "portion": "all"}
    return {"type": "paragraph", "text": text, "style": "Normal", "source_index": index,
            "custody": {"origin": "v10_derived", "v10_sources": [source]}}


class SplitCriteriaTests(unittest.TestCase):
    def test_subheadings_dropped_and_subitems_kept_with_their_criterion(self):
        blocks = [block(text, index) for index, text in enumerate([
            "Participants are eligible if:", "Age", "1. At least 18 years old.", "Disease",
            "2. Has COPD with:", "FEV1 below 70%", "a. Smoker", "Other Criteria", "3. Consents.",
        ])]
        lead, criteria, dropped = split_criteria(blocks)
        self.assertEqual([item["text"] for item in lead], ["Participants are eligible if:"])
        self.assertEqual(dropped, ["Age", "Disease", "Other Criteria"])
        self.assertEqual(len(criteria), 3)
        self.assertEqual(criterion_text(criteria[1]), "Has COPD with:\nFEV1 below 70%\na. Smoker")

    def test_text_without_numbers_is_left_alone(self):
        blocks = [block("Adults only.", 0)]
        self.assertEqual(split_criteria(blocks), (blocks, [], []))


if __name__ == "__main__":
    unittest.main()
