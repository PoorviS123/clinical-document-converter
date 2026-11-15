"""Runs the protocol-reformatter backend (a separate repo) on an uploaded protocol."""
from pathlib import Path
import os
import shutil
import subprocess

DEFAULT_BACKEND_DIR = Path(__file__).resolve().parents[2] / "protocol-reformatter"
BACKEND_DIR = Path(os.environ.get("PROTOCOL_REFORMATTER_DIR", DEFAULT_BACKEND_DIR)).resolve()
BACKEND_PYTHON = BACKEND_DIR / ".venv" / "bin" / "python"
LLM_BACKEND = os.environ.get("PROTOCOL_REFORMATTER_LLM")  # cli, codex or api; unset uses the backend default
CONVERSION_TIMEOUT_SECONDS = 30 * 60
ERROR_TAIL_CHARS = 2000
DEMO = os.environ.get("DEMO", "1").lower() not in ("0", "false", "no")
DEMO_OUTPUTS_DIR = Path(__file__).resolve().parent / "demo_outputs"
DEMO_INPUT = Path(__file__).resolve().parent / "demo_input" / "Demo-01234_Protocol_CPT-v010-3-1.docx"
OUTPUT_FILENAMES = {
    "fhir": "output.fhir.json",
    "ai_assisted": "output.ai_assisted.docx",
    "human_review": "output.human_review.docx",
    "m11": "output.m11.txt",
    "cesharp": "output.cesharp.txt",
    "usdm": "output.usdm.json",
}
# Optional files, copied when present. The run report feeds the review desk's cross-references and
# omitted sections; the protocol JSON (after AI drafting) is offered as a download.
RUN_REPORT_FILENAME = "output.report.txt"
PROTOCOL_JSON_FILENAME = "output.protocol.json"
EXTRACT_SCRIPT = Path(__file__).resolve().parents[1] / "cesharp_extract.py"
EXTRACT_TIMEOUT_SECONDS = 10 * 60
# The backend prints nothing until it finishes, but writes each file as its step completes,
# so a step is done once its file exists. The last step is ours and has no file of its own.
STEPS = [
    {"label": "Extract and place", "detail": "Read text, tables and figures and place them in the v011 structure. No AI.", "done_file": "{stem}.pre-llm.docx"},
    {"label": "AI drafting", "detail": "Draft the sections v011 needs that v010 has no text for.", "done_file": "{stem}.docx"},
    {"label": "FHIR bundle", "detail": "Build the bundle from the protocol JSON. Rule-based, no AI.", "done_file": "{stem}.fhir.json"},
    {"label": "Checks", "detail": "ICH M11 conformance and CeSHarP structure.", "done_file": "{stem}.cesharp.txt"},
    {"label": "Study data", "detail": "Read the v011 document back into USDM for the review desk.", "done_file": None},
]


def completed_step_count(output_dir: Path, stem: str) -> int:
    """How many STEPS have finished, judged by the files the backend has written so far."""
    count = 0
    for step in STEPS:
        if step["done_file"] is None or not (output_dir / step["done_file"].format(stem=stem)).is_file():
            break
        count += 1
    return count


def convert_document(input_path: str | Path, output_dir: str | Path) -> dict[str, str]:
    """Return the paths of the three outputs and two validation reports the web app serves, from the backend or the demo files."""
    if DEMO:
        return copy_demo_outputs(Path(output_dir))
    return run_backend(Path(input_path), Path(output_dir))


def copy_demo_outputs(output_dir: Path) -> dict[str, str]:
    """Copy the prepared sample outputs without calling the backend. The uploaded file is ignored."""
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for key, filename in OUTPUT_FILENAMES.items():
        shutil.copyfile(DEMO_OUTPUTS_DIR / filename, output_dir / filename)
        outputs[key] = str(output_dir / filename)
    for filename in (RUN_REPORT_FILENAME, PROTOCOL_JSON_FILENAME):
        if (DEMO_OUTPUTS_DIR / filename).is_file():
            shutil.copyfile(DEMO_OUTPUTS_DIR / filename, output_dir / filename)
    return outputs


def run_backend(input_path: Path, output_dir: Path) -> dict[str, str]:
    """Convert a v010 protocol with protocol-reformatter."""
    input_path = input_path.resolve()
    output_dir = output_dir.resolve()
    if not BACKEND_PYTHON.is_file():
        raise RuntimeError(f"protocol-reformatter venv not found at {BACKEND_PYTHON}. See its README to install.")

    # The backend names every file after the input's stem, so the stem is "source" here.
    stem = input_path.stem
    command = [str(BACKEND_PYTHON), "-m", "converter", str(input_path), "--out-dir", str(output_dir),
               "--validate-m11", "--validate-cesharp"]
    if LLM_BACKEND:
        command += ["--llm", LLM_BACKEND]
    completed = subprocess.run(
        command, cwd=BACKEND_DIR / "src", capture_output=True, text=True,
        encoding="utf-8", timeout=CONVERSION_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"protocol-reformatter failed:\n{completed.stderr[-ERROR_TAIL_CHARS:]}")

    outputs = {
        "fhir": (output_dir / f"{stem}.fhir.json", output_dir / OUTPUT_FILENAMES["fhir"]),
        "ai_assisted": (output_dir / f"{stem}.docx", output_dir / OUTPUT_FILENAMES["ai_assisted"]),
        "human_review": (output_dir / f"{stem}.pre-llm.docx", output_dir / OUTPUT_FILENAMES["human_review"]),
        "m11": (output_dir / f"{stem}.m11.txt", output_dir / OUTPUT_FILENAMES["m11"]),
        "cesharp": (output_dir / f"{stem}.cesharp.txt", output_dir / OUTPUT_FILENAMES["cesharp"]),
    }
    for produced, expected in outputs.values():
        if not produced.is_file():
            raise RuntimeError(f"protocol-reformatter did not produce {produced.name}.")
        shutil.copyfile(produced, expected)
    result = {key: str(expected) for key, (_, expected) in outputs.items()}
    optional = {f"{stem}.report.txt": RUN_REPORT_FILENAME, f"{stem}.generated.json": PROTOCOL_JSON_FILENAME}
    for produced, served in optional.items():
        if (output_dir / produced).is_file():
            shutil.copyfile(output_dir / produced, output_dir / served)
    usdm_path = extract_usdm(output_dir / OUTPUT_FILENAMES["ai_assisted"], output_dir / OUTPUT_FILENAMES["usdm"])
    if usdm_path:
        result["usdm"] = str(usdm_path)
    return result


def extract_usdm(docx_path: Path, usdm_path: Path) -> Path | None:
    """Read the docx's CeSHarP data into USDM JSON. Optional: returns None when extraction fails."""
    try:
        completed = subprocess.run(
            [str(BACKEND_PYTHON), "-X", "utf8", str(EXTRACT_SCRIPT), str(docx_path), str(usdm_path)],
            capture_output=True, text=True, encoding="utf-8", timeout=EXTRACT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return None
    return usdm_path if completed.returncode == 0 and usdm_path.is_file() else None
