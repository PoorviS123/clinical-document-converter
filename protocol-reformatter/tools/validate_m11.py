"""Validate an ICH M11 protocol .docx with the M11 rule library from usdm4_protocol.

This is the validator behind data4knowledge's study_definitions_workbench (/validate/m11-docx).
Needs Python 3.12 and the root requirements.txt.

Usage: python tools/validate_m11.py <file.docx> [--json <out.json>]

Exit code: 0 valid, 1 error-level findings, 2 could not run, 3 incomplete (no errors, but a rule
crashed or is a stub, so the document was not fully checked).
"""

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

SUCCESS = "Success"
FAILURE = "Failure"
# usdm4_protocol 0.11.0: M11_002 always raises because it needs a CT library the validator no
# longer builds. M11_GRP_TP_CT runs the same codelist checks.
KNOWN_BROKEN_RULES = {"M11_002": "crashes in usdm4_protocol 0.11.0; M11_GRP_TP_CT covers its checks"}

SEVERITY_ORDER = ("error", "warning", "info", "debug")
EXIT_CODES = {"VALID": 0, "INVALID": 1, "INCOMPLETE": 3}
EXIT_NOT_RUN = 2


class ValidatorFailed(RuntimeError):
    """The validator could not read the document, so no rule result means anything."""


@dataclass(frozen=True)
class Finding:
    severity: str
    section: str
    element: str
    message: str

    @property
    def location(self) -> str:
        return " / ".join(part for part in (self.section, self.element) if part)


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    status: str
    findings: tuple[Finding, ...] = ()
    reason: str = ""
    """Why a rule did not run (status "Exception" or "Not Implemented")."""

    @property
    def ran(self) -> bool:
        return self.status in (SUCCESS, FAILURE)


@dataclass(frozen=True)
class Validation:
    rules: tuple[RuleResult, ...]

    @property
    def findings(self) -> list[tuple[str, Finding]]:
        return [(rule.rule_id, finding) for rule in self.rules for finding in rule.findings]

    @property
    def not_run(self) -> list[RuleResult]:
        return [rule for rule in self.rules if not rule.ran]

    @property
    def verdict(self) -> str:
        if any(finding.severity == "error" for _, finding in self.findings):
            return "INVALID"
        if any(rule.rule_id not in KNOWN_BROKEN_RULES for rule in self.not_run):
            return "INCOMPLETE"
        return "VALID"

    def to_json(self) -> str:
        return json.dumps({"verdict": self.verdict, "rules": [asdict(rule) for rule in self.rules]}, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Validation":
        rules = []
        for rule in json.loads(text)["rules"]:
            findings = tuple(Finding(**finding) for finding in rule["findings"])
            rules.append(RuleResult(rule["rule_id"], rule["status"], findings, rule["reason"]))
        return cls(tuple(rules))


def validate(docx: Path) -> Validation:
    """Run every M11 rule against docx.

    The rule library reads its YAML files with the platform encoding, and on Windows (cp1252)
    most rules crash on that. Outside UTF-8 mode this runs the check in a UTF-8 child process.
    """
    if not sys.flags.utf8_mode:
        return validate_in_utf8_process(docx)

    from simple_error_log import Errors
    from usdm4_protocol.validation.m11 import M11Validator

    log = Errors()
    # Without a work_dir, raw_docx extracts the document's images into a folder next to it.
    with tempfile.TemporaryDirectory() as work_dir:
        results = M11Validator(str(docx), log, work_dir=work_dir).validate()
    if results.count() == 0:
        raise ValidatorFailed(f"The validator could not read {docx.name}.\n{log.dump().strip() or 'No rules ran.'}")

    rules = []
    for rule_id, outcome in sorted(results.outcomes.items()):
        findings = tuple(finding_from(row) for row in outcome.errors.to_dict(level=Errors.DEBUG))
        reason = (outcome.exception or "").strip().splitlines()
        rules.append(RuleResult(rule_id, outcome.status.value, findings, reason[0] if reason else ""))
    return Validation(tuple(rules))


def finding_from(row: dict) -> Finding:
    location = row.get("location") or {}
    return Finding(
        severity=row["level"].lower(),
        section=(location.get("section") or "").strip(),
        element=(location.get("element") or "").strip(),
        message=row["message"],
    )


def validate_in_utf8_process(docx: Path) -> Validation:
    with tempfile.TemporaryDirectory() as out_dir:
        json_path = Path(out_dir) / "validation.json"
        command = [sys.executable, "-X", "utf8", __file__, str(docx), "--json", str(json_path)]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        if not json_path.is_file():
            raise ValidatorFailed(completed.stderr.strip() or f"validator exited with code {completed.returncode}")
        return Validation.from_json(json_path.read_text(encoding="utf-8"))


def format_report(name: str, validation: Validation) -> str:
    findings = sorted(validation.findings, key=lambda item: (SEVERITY_ORDER.index(item[1].severity), item[0]))
    counts = {severity: sum(finding.severity == severity for _, finding in findings) for severity in SEVERITY_ORDER}
    lines = [
        f"File:          {name}",
        f"Rules:         {len(validation.rules)} ({len(validation.not_run)} did not run)",
        f"Findings:      {len(findings)} (errors={counts['error']}, warnings={counts['warning']}, info={counts['info']})",
    ]
    for rule_id, finding in findings:
        lines.append(f"  [{finding.severity.upper():7}] {rule_id}  {finding.location}")
        lines.append(f"            {finding.message}")
    for rule in validation.not_run:
        known = f" (known: {KNOWN_BROKEN_RULES[rule.rule_id]})" if rule.rule_id in KNOWN_BROKEN_RULES else ""
        lines.append(f"  [NOT RUN] {rule.rule_id} {rule.status}: {rule.reason}{known}")
    lines.append(f"RESULT: {validation.verdict}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an ICH M11 protocol .docx")
    parser.add_argument("file", type=Path)
    parser.add_argument("--json", type=Path, help="also write the per-rule results to this JSON file")
    args = parser.parse_args()

    if args.file.suffix.lower() != ".docx" or not args.file.is_file():
        print(f"Not a .docx file: {args.file}", file=sys.stderr)
        return EXIT_NOT_RUN
    try:
        validation = validate(args.file)
    except ValidatorFailed as error:
        print(error, file=sys.stderr)
        return EXIT_NOT_RUN

    print(format_report(args.file.name, validation))
    if args.json:
        args.json.write_text(validation.to_json(), encoding="utf-8")
    return EXIT_CODES[validation.verdict]


if __name__ == "__main__":
    sys.exit(main())
