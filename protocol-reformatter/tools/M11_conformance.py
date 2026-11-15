"""Key test for the generator: convert sample v010 protocols with no LLM step and check each output
against every ICH M11 validator rule.

Each sample is converted and validated once; every rule is a subtest, so a run lists exactly which
rules each generated document breaks and why. It fails until the generator's output conforms.

Usage, from the repo root: python tools/M11_conformance.py (Python 3.12)
"""

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from converter.cli import DEFAULT_CROSSWALK, DEFAULT_TEMPLATE, convert_file  # noqa: E402
from validate_m11 import KNOWN_BROKEN_RULES, RuleResult, Validation, validate  # noqa: E402

SAMPLES_DIR = REPO_ROOT / "resources" / "example-protocols" / "v010"


def convert_and_validate(sample: str) -> Validation:
    """Convert one sample without the LLM step and validate its pre-LLM docx."""
    source = SAMPLES_DIR / sample
    with tempfile.TemporaryDirectory() as out_dir:
        convert_file(source, Path(out_dir), DEFAULT_TEMPLATE, DEFAULT_CROSSWALK, backend=None, model=None)
        return validate(Path(out_dir) / f"{source.stem}.pre-llm.docx")


def describe_findings(rule: RuleResult) -> str:
    lines = [f"{rule.rule_id}: {len(rule.findings)} finding(s)"]
    for finding in rule.findings:
        lines.append(f"  [{finding.severity}] {finding.location}: {finding.message}")
    return "\n".join(lines)


class M11ConformanceChecks:
    sample: str
    validation: Validation

    @classmethod
    def setUpClass(cls) -> None:
        cls.validation = convert_and_validate(cls.sample)

    def test_every_m11_rule_passes(self) -> None:
        for rule in self.validation.rules:
            with self.subTest(rule=rule.rule_id):
                if not rule.ran and rule.rule_id in KNOWN_BROKEN_RULES:
                    self.skipTest(f"{rule.rule_id} {KNOWN_BROKEN_RULES[rule.rule_id]}")
                if not rule.ran:
                    self.fail(f"{rule.rule_id} did not run ({rule.status}): {rule.reason}")
                if rule.findings:
                    self.fail(describe_findings(rule))


class DocxInputTest(M11ConformanceChecks, unittest.TestCase):
    sample = "2025-521589-83-00_Protocol_tracked.docx"


class PdfInputTest(M11ConformanceChecks, unittest.TestCase):
    sample = "2025-522983-33-00_Protocol.pdf"


class AmendedProtocolTest(M11ConformanceChecks, unittest.TestCase):
    """An amendment makes M11 require the amendment fields on the title page."""

    sample = "2024-519298-21-00_Protocol.pdf"


if __name__ == "__main__":
    unittest.main()
