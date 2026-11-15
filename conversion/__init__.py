"""Adapter from ClinicalConverter's job contract to the protocol reformatter CLI."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_DIR / "protocol-reformatter"
BACKEND_SRC = BACKEND_DIR / "src"


def convert_document(input_path: str | Path, output_dir: str | Path) -> dict[str, str]:
    """Run the teammate CLI and return generated DOCX files for the web UI."""
    input_path = Path(input_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not (BACKEND_SRC / "converter" / "cli.py").is_file():
        raise RuntimeError(f"Protocol reformatter backend is missing at {BACKEND_DIR}")

    command = [
        sys.executable,
        "-m",
        "converter",
        str(input_path),
        "--out-dir",
        str(output_dir),
    ]
    ai_generation_available = shutil.which("codex") is not None
    if ai_generation_available:
        command.extend(["--llm", "codex"])
    else:
        command.append("--skip-generate")
    result = subprocess.run(
        command,
        cwd=BACKEND_SRC,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "No CLI error details were provided."
        raise RuntimeError(f"Protocol reformatter failed (exit {result.returncode}): {detail[-4000:]}")

    source_stem = input_path.stem
    if ai_generation_available:
        report_path = output_dir / f"{source_stem}.report.txt"
        report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
        generation_summary = re.search(
            r"drafted\s+(\d+)\s+section\(s\).*?failed\s+(\d+)\s*:", report
        )
        if generation_summary and int(generation_summary.group(1)) == 0 and int(generation_summary.group(2)) > 0:
            ai_generation_available = False

    generated_paths = {"human_review": output_dir / f"{source_stem}.pre-llm.docx"}
    output_paths = {
        "human_review": output_dir / "output.human_review.docx",
    }
    if ai_generation_available:
        generated_paths["ai_assisted"] = output_dir / f"{source_stem}.docx"
        output_paths["ai_assisted"] = output_dir / "output.ai_assisted.docx"

    for key, generated_path in generated_paths.items():
        if not generated_path.is_file():
            raise RuntimeError(f"Protocol reformatter did not create its {key} DOCX: {generated_path.name}")
        if generated_path.resolve() != output_paths[key].resolve():
            shutil.copy2(generated_path, output_paths[key])

    return {key: str(path) for key, path in output_paths.items()}
