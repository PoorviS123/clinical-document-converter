import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from converter.omit import omit_empty_optional
from converter.validate import load_schema, validate_document
from minimal_instance_builder import minimal_instance  # noqa: E402  (schema/ is on the path via converter.place's import)


class OmitEmptyOptionalTests(unittest.TestCase):
    schema = load_schema()

    def test_missing_optional_sections_are_omitted_and_required_ones_stay(self):
        protocol = minimal_instance(self.schema)
        lifestyle = protocol["sections"]["5"]["subsections"]["5.5"]
        omitted = omit_empty_optional(protocol, self.schema)
        meals = lifestyle["subsections"]["5.5.1"]
        self.assertEqual(meals["status"], "omitted")
        self.assertIn("5.5.1", omitted)
        self.assertEqual(protocol["sections"]["2"]["subsections"]["2.1"]["status"], "missing")
        errors, _ = validate_document(protocol)
        self.assertEqual(errors, [])

    def test_kept_parent_keeps_its_missing_children(self):
        protocol = minimal_instance(self.schema)
        device_definitions = protocol["sections"]["9"]["subsections"]["9.1"]["subsections"]["9.1.3"]["subsections"]["9.1.3.1"]
        adverse_event = device_definitions["subsections"]["9.1.3.1#definition_of_medical_device_ae_and_ade"]
        adverse_event["status"] = "not_applicable"
        adverse_event["custody"] = {
            "origin": "ai_generated", "v10_sources": [], "confidence": "medium", "needs_review": True,
            "actor": {"kind": "llm", "name": "claude", "model": "fake-model"},
        }
        omit_empty_optional(protocol, self.schema)
        statuses = {child["status"] for child in device_definitions["subsections"].values()}
        self.assertNotIn("omitted", statuses)
        errors, _ = validate_document(protocol)
        self.assertEqual(errors, [])

    def test_spec_optional_sections_can_be_omitted(self):
        protocol = minimal_instance(self.schema)
        omitted = omit_empty_optional(protocol, self.schema)
        for number in ("2.2.1", "2.2.2", "2.2.3", "4.1.1"):
            self.assertIn(number, omitted)


if __name__ == "__main__":
    unittest.main()
