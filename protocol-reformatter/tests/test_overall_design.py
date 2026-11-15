import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from converter.generate.fields import extract_country_regions, extract_objectives, extract, fields_description, navigate, overall_design_extraction, section_at, typed_value
from converter.render.docx import filled_text
from converter.validate import load_schema, validate_document
from minimal_instance_builder import minimal_instance  # noqa: E402  (schema/ is on the path via converter.validate)


class FakeClient:
    name = "fake"
    model = "fake"

    def __init__(self, answer):
        self.answer = answer

    def complete(self, prompt):
        return json.dumps(self.answer)


def protocol_with_synopsis(schema):
    protocol = minimal_instance(schema)
    section = section_at(protocol, "1.1.2")
    source = {"section_id": "v10:1.1", "found_number": "1.1", "found_heading": "Synopsis", "match": "exact",
              "source_indices": [0], "portion": "all"}
    custody = {"origin": "v10_derived", "v10_sources": [source], "actor": {"kind": "script", "name": "t"},
               "confidence": "high", "needs_review": False}
    section["status"] = "populated"
    section["custody"] = custody
    section["content"] = [{"type": "paragraph", "text": "Randomized, double-blind, placebo-controlled study.",
                           "style": "Normal", "source_index": 0, "custody": custody}]
    return protocol


class OverallDesignTests(unittest.TestCase):
    schema = load_schema()

    def validate(self, protocol):
        return validate_document(protocol)[0]

    def test_valid_answers_fill_slots_and_invalid_ones_stay_missing(self):
        protocol = protocol_with_synopsis(self.schema)
        client = FakeClient({
            "trial_blind_schema": "Double Blind",
            "control_type": "Placebo",
            "number_of_arms": 2,
            "blinded_roles": ["Participant", "Investigator", "Wizard"],
            "intervention_duration": {"quantity": 90, "unit": "Days"},
            "stratification": "Maybe",
            "master_protocol": None,
        })
        result = extract(protocol, client, self.schema, self.validate, overall_design_extraction(self.schema))
        fields = section_at(result, "1.1.2")["fields"]
        self.assertEqual(fields["trial_blind_schema"]["value"]["decode"], "Double Blind")
        self.assertEqual(fields["trial_blind_schema"]["status"], "filled")
        self.assertEqual(fields["trial_blind_schema"]["custody"]["origin"], "ai_transformed")
        self.assertTrue(fields["trial_blind_schema"]["custody"]["needs_review"])
        self.assertEqual(fields["number_of_arms"]["value"], 2)
        self.assertEqual(filled_text(fields, ["intervention_duration"]), "90 Days")
        self.assertEqual([role["value"]["decode"] for role in fields["blinded_roles"]], ["Participant", "Investigator"])
        self.assertEqual(fields["stratification"]["status"], "missing")
        self.assertEqual(fields["master_protocol"]["status"], "missing")
        self.assertEqual(self.validate(result), [])

    def test_unsupported_values_are_rejected(self):
        description = navigate(fields_description(self.schema, "Fields_1_1_2"), "number_of_arms")
        self.assertIsNone(typed_value(description, True))
        self.assertIsNone(typed_value(description, -1))
        self.assertIsNone(typed_value(description, "2"))

    def test_nothing_is_asked_when_the_section_has_no_source(self):
        protocol = minimal_instance(self.schema)
        result = extract(protocol, FakeClient({}), self.schema, self.validate, overall_design_extraction(self.schema))
        self.assertIn("filled 0", result["processing_log"][-1]["summary"])

    def test_country_regions_fill_an_empty_12_2_and_reject_bad_codes(self):
        protocol = protocol_with_synopsis(self.schema)
        client = FakeClient({"country_regions": [
            {"country_code": "DEU", "country_name": "Germany", "requirements": ["Use the German ICF."], "clarifications": []},
            {"country_code": "Germany", "country_name": "Germany", "requirements": ["x"], "clarifications": []},
        ]})
        result = extract_country_regions(protocol, client, self.schema, self.validate)
        section = section_at(result, "12.2")
        self.assertEqual([r["identifier"]["value"]["code"] for r in section["fields"]["country_regions"]], ["DEU"])
        self.assertEqual(section["status"], "populated")
        self.assertEqual(self.validate(result), [])

    def test_objectives_fill_section_3_and_replace_its_source_headings(self):
        protocol = protocol_with_synopsis(self.schema)
        client = FakeClient({
            "primary": [{"category": "Efficacy", "statement": "To compare drug A with placebo.",
                         "estimands": [{"endpoint": "Time to first event", "population": None,
                                        "intercurrent_events": [{"description": "Rescue", "strategy": "Treatment policy"}]}]}],
            "secondary": [{"statement": ""}],
            "exploratory": [],
        })
        result = extract_objectives(protocol, client, self.schema, self.validate)
        primary = section_at(result, "3.1")["objectives"]
        self.assertEqual(primary[0]["id"], "primary-1")
        self.assertEqual(primary[0]["estimands"][0]["endpoint"]["value"], "Time to first event")
        self.assertEqual(primary[0]["estimands"][0]["intercurrent_events"][0]["strategy"]["value"], "Treatment policy")
        self.assertEqual(section_at(result, "3.2")["status"], "not_applicable")
        self.assertEqual(self.validate(result), [])


if __name__ == "__main__":
    unittest.main()
