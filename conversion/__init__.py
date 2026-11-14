"""Conversion integration point.

Replace convert_document() with the teammate's real converter when its API is agreed.
The current implementation deliberately creates DEMO placeholders only.
"""
from pathlib import Path
import json
from docx import Document
from docx.enum.text import WD_COLOR_INDEX


def convert_document(input_path: str | Path, output_dir: str | Path) -> dict[str, str]:
    """Create three clearly labeled demo outputs and return their paths."""
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fhir_path = output_dir / "output.fhir.json"
    ai_path = output_dir / "output.ai_assisted.docx"
    human_path = output_dir / "output.human_review.docx"

    demo_fhir = {
        "resourceType": "Bundle",
        "type": "collection",
        "meta": {"tag": [{"system": "urn:clinicalconverter", "code": "DEMO-PLACEHOLDER"}]},
        "identifier": {"value": "DEMO-OUTPUT-NOT-CLINICAL-DATA"},
        "entry": [],
        "note": "Placeholder only. This is not converted clinical data and is not clinically valid."
    }
    fhir_path.write_text(json.dumps(demo_fhir, indent=2), encoding="utf-8")

    def build_docx(path: Path, ai_version: bool) -> None:
        doc = Document()
        doc.add_heading("V11 Clinical Document - DEMO", 0)
        p = doc.add_paragraph()
        p.add_run("DEMONSTRATION OUTPUT ONLY. Not a real clinical conversion. ").bold = True
        doc.add_paragraph("Section 1. Document overview", style="Heading 1")
        doc.add_paragraph("This placeholder demonstrates the V11 document output layout.")
        doc.add_paragraph("Section 2. Information requiring completion", style="Heading 1")
        p = doc.add_paragraph()
        p.add_run("Example missing section: ")
        if ai_version:
            generated = p.add_run("[DEMO AI-GENERATED PLACEHOLDER TEXT FOR REVIEW]")
            generated.font.highlight_color = WD_COLOR_INDEX.YELLOW
        else:
            p.add_run("[Intentionally left blank for human review]")
        doc.add_paragraph("Section 3. Review notes", style="Heading 1")
        doc.add_paragraph("Replace this demo converter with the team's validated conversion implementation.")
        doc.save(path)

    build_docx(ai_path, True)
    build_docx(human_path, False)
    return {"fhir": str(fhir_path), "ai_assisted": str(ai_path), "human_review": str(human_path)}
