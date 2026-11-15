import sys
import unittest
from pathlib import Path

import docx

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from check_output import check_content, protocol_json_path  # noqa: E402
from converter.audit import build_audit  # noqa: E402


def paragraph(text: str, **extra: object) -> dict[str, object]:
    return {"type": "paragraph", "text": text, **extra}


def section(number: str, title: str, content: list, status: str = "populated", **extra: object) -> dict[str, object]:
    return {"number": number, "title": title, "status": status, "content": content,
            "subsections": {}, "additional_subsections": [], **extra}


def protocol() -> dict[str, object]:
    parent = section("1", "Protocol Summary", [paragraph("Intro text.")])
    parent["subsections"] = {"1.1": section("1.1", "Synopsis", [paragraph("Synopsis text.")])}
    parent["additional_subsections"] = [
        {"title": "Extra", "content": [paragraph("Extra text.")], "subsections": []},
    ]
    omitted = section("2", "Introduction", [paragraph("Never written.")], status="omitted")
    return {"sections": {"1": parent, "2": omitted}, "unplaced_v10_content": []}


class AuditTest(unittest.TestCase):
    def test_outline_numbers_additional_subsections_and_skips_omitted(self) -> None:
        audit = build_audit(protocol())
        self.assertEqual(
            audit.outline,
            [(1, "1", "Protocol Summary"), (2, "1.1", "Synopsis"), (2, "1.2", "Extra")],
        )
        self.assertEqual([block.text for block in audit.blocks], ["Introtext.", "Synopsistext.", "Extratext."])

    def test_lead_in_replaced_by_fixed_text_is_not_expected(self) -> None:
        criteria = section("5.2", "Inclusion Criteria", [paragraph("Participants must:"), paragraph("Be adults.")])
        audit = build_audit({"sections": {"5": section("5", "Population", [], subsections={"5.2": criteria})},
                             "unplaced_v10_content": []})
        self.assertEqual([block.text for block in audit.blocks], ["Beadults."])

    def test_figures_are_counted_not_matched_by_text(self) -> None:
        audit = build_audit({"sections": {"1": section("1", "A", [paragraph("[Figure]", has_image=True)])},
                             "unplaced_v10_content": []})
        self.assertEqual((audit.figures, audit.blocks), (1, []))

    def test_check_content_reports_missing_and_out_of_order_blocks(self) -> None:
        audit = build_audit(protocol())
        document = docx.Document()
        for text in ("Synopsis text.", "Intro text.", "Extra text."):
            document.add_paragraph(text)
        problems = check_content(document, audit)
        self.assertEqual(len(problems), 1)
        self.assertIn("1 blocks missing or out of order", problems[0])

    def test_check_content_passes_when_all_blocks_present_in_order(self) -> None:
        document = docx.Document()
        for text in ("Intro  text.", "Synopsis text.", "Extra text."):
            document.add_paragraph(text)
        self.assertEqual(check_content(document, build_audit(protocol())), [])

    def test_json_pairing(self) -> None:
        self.assertEqual(protocol_json_path(Path("out/a.docx")).name, "a.generated.json")
        self.assertEqual(protocol_json_path(Path("out/a.pre-llm.docx")).name, "a.json")


if __name__ == "__main__":
    unittest.main()
