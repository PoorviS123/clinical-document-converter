import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from converter.generate.fields import Context, build_slot
from converter.terminology import Term, TermRegistry
from converter.terminology.ncit import NcitSource

TABLET_SUBSET = "CDISC SDTM Dosage Form Terminology"


def ncit_row(code, names, semantic_type, subsets):
    return "\t".join([code, "url", "parent", "|".join(names), "definition", "", "", semantic_type, "|".join(subsets)])


class FakeClient:
    name = "fake"
    model = "test-model"


class NcitSourceTests(unittest.TestCase):
    def source(self, rows, **filters):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "Thesaurus.txt"
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return NcitSource("CDISC SDTM", path=path, **filters)

    def test_finds_a_subset_concept_by_synonym_ignoring_case(self):
        source = self.source([ncit_row("C42998", ["Tablet Dosage Form", "TABLET", "Tab"], "Biomedical", [TABLET_SUBSET])],
                             subset=TABLET_SUBSET)
        self.assertEqual(source.find("tab"), [Term("CDISC SDTM", "C42998", "Tablet Dosage Form")])

    def test_ignores_concepts_outside_the_subset_and_types(self):
        source = self.source([ncit_row("C1", ["Tablet"], "Finding", ["Other Terminology"])], subset=TABLET_SUBSET)
        self.assertEqual(source.find("tablet"), [])

    def test_missing_file_finds_nothing(self):
        source = NcitSource("other", subset=TABLET_SUBSET, path=Path("missing.txt"))
        self.assertEqual(source.find("tablet"), [])


class StubSource:
    def __init__(self, *terms):
        self.terms = list(terms)

    def find(self, text):
        return self.terms


class RegistryTests(unittest.TestCase):
    def test_ambiguous_match_resolves_to_nothing(self):
        registry = TermRegistry({"route": [StubSource(Term("x", "C1", "A"), Term("x", "C2", "B"))]})
        self.assertIsNone(registry.resolve("route", "a"))

    def test_falls_through_to_the_next_source_when_the_first_has_no_match(self):
        term = Term("x", "C2", "B")
        registry = TermRegistry({"route": [StubSource(), StubSource(term)]})
        self.assertEqual(registry.resolve("route", "b"), term)

    def test_unknown_field_resolves_to_nothing(self):
        self.assertIsNone(TermRegistry({}).resolve("route", "oral"))


class BuildSlotTests(unittest.TestCase):
    def test_external_term_with_a_match_becomes_a_filled_coded_slot(self):
        context = Context(terms=TermRegistry({"route": [StubSource(Term("CDISC SDTM", "C38276", "Intravenous"))]}))
        slot = build_slot({"kind": "external", "lookup": "route"}, "IV", context, FakeClient())
        self.assertEqual(slot["status"], "filled")
        self.assertEqual(slot["value"], {"system": "CDISC SDTM", "code": "C38276", "decode": "Intravenous"})
        self.assertEqual(slot["raw_text"], "IV")

    def test_external_term_without_a_match_stays_unsure_source_text(self):
        context = Context(terms=TermRegistry({}))
        slot = build_slot({"kind": "external", "lookup": "route"}, "by osmosis", context, FakeClient())
        self.assertEqual((slot["status"], slot["value"], slot["raw_text"]), ("unsure", None, "by osmosis"))


if __name__ == "__main__":
    unittest.main()
