import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from converter.title_page import parse_date, phase_decode, render_values


class TitlePageSlotTests(unittest.TestCase):
    def test_phase_decode(self):
        self.assertEqual(phase_decode("Phase 3"), "Phase 3")
        self.assertEqual(phase_decode("Phase III"), "Phase 3")
        self.assertEqual(phase_decode("2/3"), "Phase 2/Phase 3")
        self.assertIsNone(phase_decode("pivotal"))

    def test_parse_date(self):
        self.assertEqual(parse_date("05 March 2025"), "2025-03-05")
        self.assertEqual(parse_date("2025-03-05"), "2025-03-05")
        self.assertIsNone(parse_date("March-ish"))

    def test_render_values_prefers_raw_text(self):
        page = {
            "version_date": {"value": "2025-03-05", "status": "filled", "raw_text": "05 March 2025"},
            "full_title": {"value": "A Study", "status": "filled"},
            "regulatory_identifiers": [{"type": "nct_number", "label": "NCT", "id": {"value": "NCT0123", "status": "filled"}}],
            "sponsors": [{"name": {"value": None, "status": "missing"}}],
        }
        values = render_values(page)
        self.assertEqual(values["date"], "05 March 2025")
        self.assertEqual(values["regulatory_ids"], "NCT: NCT0123")
        self.assertNotIn("sponsor", values)


if __name__ == "__main__":
    unittest.main()
