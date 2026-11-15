import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdf_to_cpt_docx import Document, Heading, Paragraph, Span
from restructure import (
    MAX_HEADING_LEVEL, NO_CONTENT_PLACEHOLDER, iter_nodes, load_crosswalk, restructure, template_outline,
)
from section_splitters import (
    ACCOUNTABILITY_NODE, ASSIGNMENT_NODE, PREPARATION_NODE, RANDOMIZATION_NODE, STORAGE_NODE,
    handling_child_target, split_assignment, split_handling,
)
from converter.cli import DEFAULT_CROSSWALK, DEFAULT_TEMPLATE


def paragraph(text, kind="body"):
    return Paragraph([Span(text, False, False, False, 10)], kind=kind, page=5)


def nodes_of(split):
    return {node: [block.text for block in blocks] for node, blocks in split.parts.items()}


class HandlingSplitTests(unittest.TestCase):
    def test_keywords_route_each_paragraph(self):
        blocks = [paragraph("The product is reconstituted with saline."),
                  paragraph("Vials are stored at 2 to 8 C."),
                  paragraph("The site must reconcile all returned kits."),
                  paragraph("Contact the sponsor with questions.")]
        parts = nodes_of(split_handling(blocks))
        self.assertEqual(parts[PREPARATION_NODE], [blocks[0].text])
        self.assertEqual(parts[STORAGE_NODE], [blocks[1].text, blocks[3].text])
        self.assertEqual(parts[ACCOUNTABILITY_NODE], [blocks[2].text])

    def test_every_block_lands_once_and_order_is_kept(self):
        blocks = [paragraph(text) for text in ("Dispense it.", "Store it.", "Dilute it.", "Return it.", "Other.")]
        parts = split_handling(blocks).parts
        placed = [block for node_blocks in parts.values() for block in node_blocks]
        self.assertEqual(sorted(map(id, placed)), sorted(map(id, blocks)))
        self.assertEqual([block.text for block in parts[ACCOUNTABILITY_NODE]], ["Dispense it.", "Return it."])

    def test_bare_bullet_mark_stays_with_previous_list_item(self):
        blocks = [paragraph("Prepare the dose as follows:"), paragraph("•", "bullet")]
        self.assertEqual(list(split_handling(blocks).parts), [PREPARATION_NODE])

    def test_child_titles(self):
        self.assertEqual(handling_child_target("Packaging, Labeling, and Storage"), STORAGE_NODE)
        self.assertEqual(handling_child_target("Supply and Disposition of Study Intervention(s)"), ACCOUNTABILITY_NODE)
        self.assertEqual(handling_child_target("Accountability of Study Intervention(s)"), ACCOUNTABILITY_NODE)
        self.assertEqual(handling_child_target("Preparation of Doses"), PREPARATION_NODE)
        self.assertIsNone(handling_child_target("Something else"))


class AssignmentSplitTests(unittest.TestCase):
    def test_rules(self):
        blocks = [paragraph("Each participant gets a unique identifier at screening number assignment."),
                  paragraph("Participants are randomized 1:1 via IRT."),
                  paragraph("Study intervention is dispensed at each visit."),
                  paragraph("Randomization via IRT triggers kit dispensing."),
                  paragraph("Some other sentence.")]
        parts = nodes_of(split_assignment(blocks))
        self.assertEqual(parts[ASSIGNMENT_NODE], [blocks[0].text])
        self.assertEqual(parts[ACCOUNTABILITY_NODE], [blocks[2].text])
        self.assertEqual(parts[RANDOMIZATION_NODE], [blocks[1].text, blocks[3].text, blocks[4].text])


class RestructureTests(unittest.TestCase):
    outline = template_outline(DEFAULT_TEMPLATE)
    crosswalk = load_crosswalk(DEFAULT_CROSSWALK)

    def restructure_body(self, body):
        return restructure(Document(body=body), self.crosswalk, self.outline)

    def test_handling_and_assignment_fill_all_three_handling_headings(self):
        result = self.restructure_body([
            Heading("6", "Study Intervention and Concomitant Therapy", 5),
            Heading("6.2", "Preparation, Handling, Storage, and Accountability", 5),
            paragraph("Kits are stored in a secure area."),
            Heading("6.3", "Assignment to Study Intervention", 6),
            paragraph("Participants are randomized via IRT."),
            paragraph("Returned kits are not re-dispensed."),
        ])
        by_title = {node.title: node for node in iter_nodes(result.nodes)}
        prep = by_title["Preparation of Investigational Trial Intervention"]
        storage = by_title["Storage and Handling of Investigational Trial Intervention"]
        accountability = by_title["Accountability of Investigational Trial Intervention"]
        self.assertEqual([block.text for block in prep.blocks], [NO_CONTENT_PLACEHOLDER])
        self.assertEqual([block.text for block in storage.blocks], ["Kits are stored in a secure area."])
        self.assertEqual(accountability.blocks[0].text, "Returned kits are not re-dispensed.")
        self.assertEqual(result.number_map["6.2"], storage.number)
        self.assertEqual(result.number_map["6.3"], by_title["Randomization"].number)
        self.assertTrue(any("6.2" in line and "split by keyword" in line for line in result.report.split_review))

    def test_sections_below_the_deepest_heading_style_are_folded_into_their_parent(self):
        """9.4.1 maps to a level-4 node, so 9.4.x.y.z sources would exceed the six heading styles."""
        body = [Heading("9", "Statistical Considerations", 8), Heading("9.4", "Analyses Supporting Secondary Objective(s)", 8),
                Heading("9.4.1", "Analyses Supporting Secondary Objective", 8)]
        title_words = ["Alpha", "Bravo", "Charlie", "Delta"]
        number = "9.4.1"
        for depth, word in enumerate(title_words, start=1):
            number += ".1"
            body += [Heading(number, f"{word} Analysis", 9), paragraph(f"{word} text.")]
        result = self.restructure_body(body)
        levels = [node.level for node in iter_nodes(result.nodes)]
        self.assertLessEqual(max(levels), MAX_HEADING_LEVEL)
        texts = [block.text for node in iter_nodes(result.nodes) for block in node.blocks if isinstance(block, Paragraph)]
        for word in title_words:
            self.assertIn(f"{word} text.", texts)

    def test_sponsor_subsection_fills_the_template_section_its_title_names(self):
        result = self.restructure_body([
            Heading("6", "Study Intervention and Concomitant Therapy", 5),
            Heading("6.9", "Prior and Concomitant Therapy", 5),
            Heading("6.9.1", "Prohibited Interventions and Procedures", 5),
            paragraph("No live vaccines."),
            Heading("6.9.2", "Permitted Medications and Procedures", 5),
            paragraph("Paracetamol is allowed."),
            Heading("6.9.3", "Dietary Supplements", 5),
            paragraph("Report all supplements."),
        ])
        by_title = {node.title: node for node in iter_nodes(result.nodes)}
        prohibited = by_title["Prohibited Concomitant Therapy"]
        permitted = by_title["Permitted Concomitant Therapy"]
        self.assertEqual([block.text for block in prohibited.blocks], ["No live vaccines."])
        self.assertEqual([block.text for block in permitted.blocks], ["Paracetamol is allowed."])
        self.assertTrue(by_title["Dietary Supplements"].from_template is False)
        self.assertEqual([line.split(" ")[0] for line in result.report.unmatched], ["6.9.3"])

    def test_template_number_survives_pruning_of_empty_siblings(self):
        result = self.restructure_body([
            Heading("8", "Study Assessments and Procedures", 5),
            Heading("8.3", "Safety Assessments", 5),
            Heading("8.3.1", "Physical Examinations", 5),
            paragraph("Full exam."),
            Heading("8.3.4", "Clinical Safety Laboratory Tests", 5),
            paragraph("Blood draws."),
        ])
        labs = next(node for node in iter_nodes(result.nodes) if node.blocks and node.blocks[0].text == "Blood draws.")
        self.assertEqual(labs.template_number, "8.4.4")
        self.assertNotEqual(labs.number, labs.template_number)


if __name__ == "__main__":
    unittest.main()
