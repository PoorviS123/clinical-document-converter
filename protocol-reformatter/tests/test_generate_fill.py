import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from converter.generate.client import LlmError, make_client, parse_cli_output
from converter.generate.fill import generate_missing, missing_sections
from converter.generate.guidance import section_guidance, style_example
from converter.generate.prompts import MAX_DRAFT_WORDS, build_prompt


class FakeClient:
    name = "fake"
    model = "fake-model"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def make_protocol():
    populated_custody = {"origin": "v10_verbatim", "v10_sources": [], "actor": {"kind": "script", "name": "x"},
                         "confidence": "high", "needs_review": False}
    return {
        "sections": {
            "4": {"number": "4", "title": "Trial Design", "status": "populated", "custody": populated_custody,
                  "content": [{"type": "paragraph", "text": "Duration is 12 weeks.", "style": "Normal",
                               "custody": populated_custody}],
                  "subsections": {"4.2.4": {"number": "4.2.4", "title": "Rationale for Trial Duration",
                                            "status": "missing", "custody": {"origin": "none"}, "content": [],
                                            "x-guidance": ["Justify the duration."]}}},
        },
        "processing_log": [],
    }


class FillTests(unittest.TestCase):
    def test_populated_draft_is_ai_generated_and_flagged(self):
        reply = json.dumps({"status": "populated", "paragraphs": ["Twelve weeks covers the endpoint."],
                            "basis": ["4"], "confidence": "medium"})
        client = FakeClient([reply])
        result = generate_missing(make_protocol(), client)
        section = result["sections"]["4"]["subsections"]["4.2.4"]
        self.assertEqual(section["status"], "populated")
        self.assertEqual(section["custody"]["origin"], "ai_generated")
        self.assertTrue(section["custody"]["needs_review"])
        self.assertEqual(section["content"][0]["custody"]["actor"]["model"], "fake-model")
        self.assertIn("Duration is 12 weeks.", client.prompts[0])
        self.assertEqual(len(missing_sections(make_protocol())), 1)
        self.assertEqual(len(missing_sections(result)), 0)

    def test_input_protocol_is_not_mutated(self):
        original = make_protocol()
        reply = json.dumps({"status": "populated", "paragraphs": ["x"], "confidence": "high"})
        generate_missing(original, FakeClient([reply]))
        self.assertEqual(original, make_protocol())

    def test_invalid_json_retries_then_leaves_missing(self):
        client = FakeClient(["not json", "still not json"])
        result = generate_missing(make_protocol(), client)
        self.assertEqual(result["sections"]["4"]["subsections"]["4.2.4"]["status"], "missing")
        self.assertIn("failed 1", result["processing_log"][-1]["summary"])

    def test_m11_guidance_reaches_the_prompt(self):
        reply = json.dumps({"status": "missing", "paragraphs": []})
        client = FakeClient([reply])
        generate_missing(make_protocol(), client)
        self.assertIn("trial duration is appropriate", client.prompts[0])
        self.assertIn("STYLE EXAMPLE", client.prompts[0])

    def test_guidance_falls_back_to_cpt_key_instructions(self):
        self.assertTrue(section_guidance("2.2.1") or section_guidance("4.2.2"))
        self.assertEqual(section_guidance("99.9"), [])
        self.assertIsNone(style_example("99.9"))

    def test_prompt_without_example_has_no_example_block(self):
        self.assertNotIn("STYLE EXAMPLE", build_prompt("4.2.4", "Duration", ["g"], "source"))

    def test_overlong_draft_is_rejected(self):
        long_text = " ".join(["word"] * (MAX_DRAFT_WORDS + 1))
        reply = json.dumps({"status": "populated", "paragraphs": [long_text], "confidence": "high"})
        result = generate_missing(make_protocol(), FakeClient([reply, reply]))
        self.assertEqual(result["sections"]["4"]["subsections"]["4.2.4"]["status"], "missing")
        self.assertIn("failed 1", result["processing_log"][-1]["summary"])

    def test_retry_succeeds_on_second_attempt(self):
        good = json.dumps({"status": "populated", "paragraphs": ["ok"], "confidence": "low"})
        client = FakeClient([LlmError("boom"), good])
        result = generate_missing(make_protocol(), client)
        self.assertEqual(result["sections"]["4"]["subsections"]["4.2.4"]["status"], "populated")

    def test_sections_with_v10_source_are_not_drafted(self):
        client = FakeClient([])
        result = generate_missing(make_protocol(), client, has_v10_source=frozenset({"4.2.4"}))
        self.assertEqual(client.prompts, [])
        self.assertEqual(result["sections"]["4"]["subsections"]["4.2.4"]["status"], "missing")

    def test_no_basis_stays_missing(self):
        client = FakeClient([json.dumps({"status": "missing", "paragraphs": []})])
        result = generate_missing(make_protocol(), client)
        self.assertEqual(result["sections"]["4"]["subsections"]["4.2.4"]["status"], "missing")
        self.assertEqual(len(client.prompts), 1)
        self.assertIn("no basis in source for 1", result["processing_log"][-1]["summary"])

    def test_not_applicable_has_no_content(self):
        client = FakeClient([json.dumps({"status": "not_applicable", "paragraphs": ["ignored"], "confidence": "high"})])
        section = generate_missing(make_protocol(), client)["sections"]["4"]["subsections"]["4.2.4"]
        self.assertEqual(section["status"], "not_applicable")
        self.assertEqual(section["content"], [])


class ClientTests(unittest.TestCase):
    def test_parse_cli_output(self):
        self.assertEqual(parse_cli_output(json.dumps({"is_error": False, "result": "hi"})), "hi")
        with self.assertRaises(LlmError):
            parse_cli_output(json.dumps({"is_error": True, "result": "bad"}))
        with self.assertRaises(LlmError):
            parse_cli_output("garbage")

    def test_unknown_backend(self):
        with self.assertRaises(ValueError):
            make_client("nope")


if __name__ == "__main__":
    unittest.main()
