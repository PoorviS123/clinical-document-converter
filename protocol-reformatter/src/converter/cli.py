"""Convert v010 protocol PDFs or Word files to v011: extract, place, render, LLM-generate missing sections, render again."""

import argparse
import json
from pathlib import Path

from converter.extract.pdf import load_pdf
from converter.generate.client import DEFAULT_BACKEND, make_client
from converter.generate.fields import extract_structured_fields
from converter.generate.fill import generate_missing
from converter.omit import omit_empty_optional
from converter.place import place_document
from converter.render.docx import render_docx
from converter.validate import load_schema, validate_document
from restructure import load_crosswalk, template_outline



RESOURCES = Path(__file__).resolve().parents[2] / "resources"
DEFAULT_TEMPLATE = RESOURCES / "CPT_CoreBWE-v011.docx"
DEFAULT_CROSSWALK = RESOURCES / "research" / "crosswalk.tsv"


PDF_SUFFIX = ".pdf"
DOCX_SUFFIX = ".docx"
SUPPORTED_SUFFIXES = (PDF_SUFFIX, DOCX_SUFFIX)


def load_docx(path: Path):
    """Imported on first use so PDF conversion works without the docx extractor."""
    from converter.extract.docx import load_docx as load
    return load(path)


def load_source(path: Path):
    """Extract the Document with the loader that matches the file extension."""
    suffix = path.suffix.lower()
    if suffix == PDF_SUFFIX:
        return load_pdf(path)
    if suffix == DOCX_SUFFIX:
        return load_docx(path)
    raise ValueError(f"{path}: unsupported input type {path.suffix!r}; expected one of {', '.join(SUPPORTED_SUFFIXES)}")


class ValidationFailed(RuntimeError):
    pass


def crosswalk_targets(crosswalk: dict) -> frozenset[str]:
    """v011 section numbers that some v010 section maps to."""
    return frozenset(
        number for row in crosswalk.values() for number in [row.target, *row.also] if number
    )


def check(protocol: dict[str, object], stage: str) -> None:
    errors, _ = validate_document(protocol)
    if errors:
        shown = "\n".join(errors[:10])
        raise ValidationFailed(f"{stage} JSON failed schema validation ({len(errors)} errors):\n{shown}")


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def convert_file(source_path: Path, out_dir: Path, template: Path, crosswalk_path: Path,
                  backend: str | None, model: str | None) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = source_path.stem
    assets_dir = out_dir / f"{name}.assets"
    schema = load_schema()
    crosswalk = load_crosswalk(crosswalk_path)
    source = load_source(source_path)
    protocol, layout, report = place_document(
        source, source_path, schema, crosswalk, template_outline(template),
        assets_dir, str(template.relative_to(RESOURCES.parent)),
    )
    check(protocol, "placed")
    write_json(out_dir / f"{name}.json", protocol)
    write_json(out_dir / f"{name}.layout.json", layout)
    render_docx(protocol, layout, assets_dir, template, out_dir / f"{name}.pre-llm.docx", name)
    lines = [f"{name}: placed {len(layout)} blocks", *report.unmatched, *report.notes,
             *(f"docx: {note}" for note in getattr(source, "notes", []))]
    if backend is not None:
        client = make_client(backend, model)
        def schema_errors(candidate: dict[str, object]) -> list[str]:
            return validate_document(candidate)[0]

        generated = generate_missing(protocol, client, schema_errors, schema, crosswalk_targets(crosswalk))
        generated = extract_structured_fields(generated, client, schema, schema_errors)
        omit_empty_optional(generated, schema)
        check(generated, "generated")
        write_json(out_dir / f"{name}.generated.json", generated)
        render_docx(generated, layout, assets_dir, template, out_dir / f"{name}.docx", name)
        lines.extend(entry["summary"] for entry in generated["processing_log"][-8:])
    (out_dir / f"{name}.report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+", help="v010 protocols (.pdf or .docx)")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--llm", choices=["cli", "codex", "api"], default=DEFAULT_BACKEND)
    parser.add_argument("--model", help="model name for the chosen backend")
    parser.add_argument("--skip-generate", action="store_true", help="stop after the pre-LLM docx")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--crosswalk", type=Path, default=DEFAULT_CROSSWALK)
    args = parser.parse_args()
    backend = None if args.skip_generate else args.llm
    for path in args.inputs:
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise SystemExit(f"{path}: unsupported input type {path.suffix!r}; expected .pdf or .docx")
        for line in convert_file(path, args.out_dir, args.template, args.crosswalk, backend, args.model):
            print(line)


if __name__ == "__main__":
    main()
