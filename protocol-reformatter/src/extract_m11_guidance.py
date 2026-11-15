"""Extract the per-section "User Guidance" text from the ICH M11 Technical Specification PDF.

Usage: python extract_m11_guidance.py <TechnicalSpecification.pdf> <out.json>
Output maps a section number ("4.2.4") to the guidance paragraphs the spec gives for that section's text element.
"""

import json
import re
import subprocess
import sys

GUIDANCE_START = re.compile(r"^\s*User Guidance\s{2,}(.*)$")
SECTION_OF_ELEMENT = re.compile(r"^\s*Relationship content\s{2,}(\d+(?:\.\d+)*)\b")
CONTINUATION_INDENT = 26
NOT_APPLICABLE = {"N/A", ""}


def guidance_by_section(spec_text: str) -> dict[str, str]:
    """Each data element lists its User Guidance first, then `Relationship content <section number>`."""
    result: dict[str, list[str]] = {}
    collecting: list[str] | None = None
    pending = ""
    for raw_line in spec_text.splitlines():
        line = raw_line.replace("\f", "")  # a page break glued to the first line would hide its indent
        start = GUIDANCE_START.match(line)
        if start:
            collecting = [start.group(1).strip()]
            pending = ""
            continue
        indent = len(line) - len(line.lstrip())
        if collecting is not None and line.strip() and indent >= CONTINUATION_INDENT:
            collecting.append(line.strip())
            continue
        if collecting is not None and line.strip():
            pending = " ".join(collecting).strip()
            collecting = None
        element = SECTION_OF_ELEMENT.match(line)
        if element and pending not in NOT_APPLICABLE:
            result.setdefault(element.group(1), []).append(pending)
        if element:
            pending = ""
    return {number: " ".join(dict.fromkeys(texts)) for number, texts in result.items()}


def main() -> None:
    pdf_path, out_path = sys.argv[1], sys.argv[2]
    spec_text = subprocess.run(
        ["pdftotext", "-layout", pdf_path, "-"], capture_output=True, text=True, check=True
    ).stdout
    guidance = guidance_by_section(spec_text)
    with open(out_path, "w") as out_file:
        json.dump(guidance, out_file, indent=1, ensure_ascii=False)
        out_file.write("\n")
    print(f"{len(guidance)} sections with guidance")


if __name__ == "__main__":
    main()
