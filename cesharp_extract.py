"""Read the CeSHarP (ICH M11) structure of a protocol .docx into USDM JSON.

Runs under the ADAPT venv, which has usdm4_protocol installed. Same import step as
ADAPT/tools/validate_cesharp.py, but the USDM is kept instead of validated.

Usage: python -X utf8 cesharp_extract.py <file.docx> <out.json>
"""
import sys
import tempfile
from pathlib import Path

from usdm4_protocol import USDM4M11

EXIT_FAILED = 1
EXIT_USAGE = 2


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return EXIT_USAGE
    docx_path, json_path = Path(sys.argv[1]), Path(sys.argv[2])
    with tempfile.TemporaryDirectory() as work_dir:
        importer = USDM4M11()
        wrapper = importer.from_docx(str(docx_path), work_dir=work_dir)
        if wrapper is None:
            print(f"The M11 importer could not read {docx_path.name}.\n{importer.errors.dump().strip()}", file=sys.stderr)
            return EXIT_FAILED
        json_path.write_text(wrapper.model_dump_json(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
