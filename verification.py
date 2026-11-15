"""Summarize the validator reports (tools/validate_m11.py, tools/validate_cesharp.py) for the results page."""
from pathlib import Path
import json
import re

REPORTS = {
    "m11": "ICH M11 conformance",
    "cesharp": "CeSHarP structure (USDM)",
}
FINDINGS_PATTERN = re.compile(r"^Findings:\s+(\d+) \(errors=(\d+), warnings=(\d+), info=(\d+)\)", re.MULTILINE)
RESULT_PATTERN = re.compile(r"^RESULT:\s*(\w+)", re.MULTILINE)
NOT_RUN = "NOT RUN"


def summarize_report(kind: str, report_path: Path) -> dict[str, object]:
    """Read a validator report. A report without a RESULT line means the validator could not run."""
    summary: dict[str, object] = {"kind": kind, "label": REPORTS[kind], "result": NOT_RUN}
    if not report_path.is_file():
        return summary
    text = report_path.read_text(encoding="utf-8", errors="replace")
    result = RESULT_PATTERN.search(text)
    findings = FINDINGS_PATTERN.search(text)
    if result and findings:
        _, errors, warnings, info = (int(count) for count in findings.groups())
        summary.update(result=result.group(1).upper(), errors=errors, warnings=warnings, info=info)
    return summary


def summarize_usdm(usdm_path: Path) -> dict[str, object] | None:
    """Count the main study elements in the USDM JSON read from the V11 document. None if it wasn't extracted."""
    if not usdm_path.is_file():
        return None
    study = json.loads(usdm_path.read_text(encoding="utf-8"))["study"]
    version = study["versions"][0]
    design = version["studyDesigns"][0] if version["studyDesigns"] else {}
    return {
        "study_name": study["name"],
        "interventions": len(version["studyInterventions"]),
        "arms": len(design.get("arms", [])),
        "objectives": len(design.get("objectives", [])),
        "sections": len(version["narrativeContentItems"]),
    }
