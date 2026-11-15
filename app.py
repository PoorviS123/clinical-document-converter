from pathlib import Path
from uuid import uuid4
import json
import zipfile
import re
import shutil
import threading
import time

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for
from pypdf import PdfReader
from werkzeug.utils import secure_filename

from conversion import DEMO, DEMO_INPUT, PROTOCOL_JSON_FILENAME, RUN_REPORT_FILENAME, STEPS, completed_step_count, convert_document
from review import summarize_review
from verification import REPORTS, summarize_report, summarize_usdm

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
MAX_BYTES = 100 * 1024 * 1024
ALLOWED = {".pdf", ".docx"}
BACKEND_INPUT_STEM = "source"
RUNNING, DONE, FAILED = "running", "done", "failed"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES


def job_folder(job_id: str) -> Path:
    # Job IDs are generated as 32-character hex strings. Reject path-like input.
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        abort(404)
    folder = OUTPUT_DIR / job_id
    if not (folder / "manifest.json").is_file():
        abort(404)
    return folder


def validate_content(path: Path, extension: str) -> None:
    if path.stat().st_size == 0:
        raise ValueError("The selected file is empty.")
    if extension == ".pdf":
        with path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise ValueError("This file does not appear to be a valid PDF.")
        try:
            reader = PdfReader(str(path), strict=False)
            if reader.is_encrypted:
                raise ValueError("Password-protected PDFs are not supported.")
            if len(reader.pages) == 0:
                raise ValueError("The PDF contains no pages.")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("The PDF could not be read. It may be damaged.") from exc
    elif extension == ".docx":
        if not zipfile.is_zipfile(path):
            raise ValueError("This file does not appear to be a valid DOCX document.")
        try:
            with zipfile.ZipFile(path) as archive:
                bad_file = archive.testzip()
                if bad_file or "word/document.xml" not in archive.namelist():
                    raise ValueError("The DOCX file is incomplete or damaged.")
        except zipfile.BadZipFile as exc:
            raise ValueError("The DOCX file is damaged.") from exc


@app.errorhandler(413)
def too_large(_error):
    return jsonify(error="File is too large. Maximum upload size is 100 MB."), 413


@app.route("/")
def home():
    demo_file = {"name": DEMO_INPUT.name, "size_mb": DEMO_INPUT.stat().st_size / 1024 / 1024} if DEMO else None
    return render_template("index.html", demo_file=demo_file)


@app.route("/api/convert", methods=["POST"])
def upload_and_convert():
    uploaded = None if DEMO else request.files.get("file")
    if not DEMO and (uploaded is None or not uploaded.filename):
        return jsonify(error="Choose a PDF or DOCX file first."), 400

    raw_name = DEMO_INPUT.name if DEMO else uploaded.filename
    extension = Path(raw_name).suffix.lower()
    if extension not in ALLOWED:
        return jsonify(error="Unsupported file type. Please upload a PDF or DOCX."), 400

    safe_name = secure_filename(raw_name) or f"document{extension}"
    job_id = uuid4().hex
    job_upload_dir = UPLOAD_DIR / job_id
    job_output_dir = OUTPUT_DIR / job_id
    job_upload_dir.mkdir(parents=True, exist_ok=True)
    job_output_dir.mkdir(parents=True, exist_ok=True)
    input_path = job_upload_dir / f"{BACKEND_INPUT_STEM}{extension}"
    if DEMO:
        shutil.copyfile(DEMO_INPUT, input_path)
    else:
        uploaded.save(input_path)

    try:
        validate_content(input_path, extension)
    except ValueError as exc:
        shutil.rmtree(job_upload_dir, ignore_errors=True)
        shutil.rmtree(job_output_dir, ignore_errors=True)
        return jsonify(error=str(exc)), 400

    manifest = {
        "job_id": job_id,
        "original_filename": safe_name,
        "demo": DEMO,
        "status": RUNNING,
        "started_at": time.time(),
    }
    write_manifest(job_output_dir, manifest)
    # Conversion takes minutes, so it runs in the background and the progress page polls its status.
    threading.Thread(target=run_job, args=(manifest, input_path, job_upload_dir, job_output_dir), daemon=True).start()
    return jsonify(success=True, job_id=job_id, progress_url=f"/jobs/{job_id}", demo=DEMO), 202


def write_manifest(job_output_dir: Path, manifest: dict) -> None:
    # Written to a temporary file first so a status poll never reads a half-written manifest.
    temporary = job_output_dir / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(job_output_dir / "manifest.json")


def run_job(manifest: dict, input_path: Path, job_upload_dir: Path, job_output_dir: Path) -> None:
    try:
        outputs = convert_document(input_path, job_output_dir)
        expected = {
            "fhir": job_output_dir / "output.fhir.json",
            "ai_assisted": job_output_dir / "output.ai_assisted.docx",
            "human_review": job_output_dir / "output.human_review.docx",
        }
        for key, expected_path in expected.items():
            returned_path = Path(outputs[key]).resolve()
            if returned_path != expected_path.resolve() or not expected_path.is_file():
                raise RuntimeError(f"Converter did not produce the expected {key} output.")
        # Ensure the JSON output is parseable before presenting a successful result.
        json.loads(expected["fhir"].read_text(encoding="utf-8"))
        for key in ("ai_assisted", "human_review"):
            with zipfile.ZipFile(expected[key]) as archive:
                if "word/document.xml" not in archive.namelist() or archive.testzip():
                    raise RuntimeError(f"Converter produced an invalid {key} DOCX.")
        manifest.update(
            status=DONE,
            verification=[summarize_report(kind, job_output_dir / Path(outputs[kind]).name) for kind in REPORTS],
            usdm=summarize_usdm(job_output_dir / "output.usdm.json"),
            outputs={key: Path(value).name for key, value in outputs.items()},
        )
    except Exception:
        app.logger.exception("Conversion failed for job %s", manifest["job_id"])
        manifest.update(status=FAILED, error="The conversion failed. Please try another file.")
    manifest["finished_at"] = time.time()
    write_manifest(job_output_dir, manifest)
    if manifest["status"] == FAILED:
        shutil.rmtree(job_upload_dir, ignore_errors=True)


def job_status(manifest: dict, folder: Path) -> dict:
    """The job's state for the progress page. Manifests from before background jobs have no status and are done."""
    status = manifest.get("status", DONE)
    done_count = len(STEPS) if status == DONE else completed_step_count(folder, BACKEND_INPUT_STEM)
    steps = []
    for index, step in enumerate(STEPS):
        if index < done_count:
            state = "done"
        elif index == done_count:
            state = "failed" if status == FAILED else "active"
        else:
            state = "waiting"
        steps.append({"label": step["label"], "detail": step["detail"], "state": state})
    return {
        "status": status,
        "steps": steps,
        "done_count": done_count,
        "started_at": manifest.get("started_at"),
        "finished_at": manifest.get("finished_at"),
        "error": manifest.get("error"),
        "results_url": f"/results/{manifest['job_id']}",
    }


@app.route("/jobs/<job_id>")
def progress(job_id):
    folder = job_folder(job_id)
    manifest = read_manifest(folder)
    if manifest.get("status", DONE) == DONE:
        return redirect(url_for("results", job_id=job_id))
    return render_template("progress.html", job=manifest, status=job_status(manifest, folder))


@app.route("/api/jobs/<job_id>/status")
def status(job_id):
    folder = job_folder(job_id)
    return jsonify(job_status(read_manifest(folder), folder))


def read_manifest(folder: Path) -> dict:
    return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))


@app.route("/results/<job_id>")
def results(job_id):
    folder = job_folder(job_id)
    manifest = read_manifest(folder)
    if manifest.get("status", DONE) != DONE:
        return redirect(url_for("progress", job_id=job_id))
    review = summarize_review(folder / "output.fhir.json", folder / RUN_REPORT_FILENAME)
    has_protocol_json = (folder / PROTOCOL_JSON_FILENAME).is_file()
    return render_template("results.html", job=manifest, review=review, has_protocol_json=has_protocol_json)


@app.route("/files/<job_id>/<kind>")
def download_output(job_id, kind):
    allowed = {
        "fhir": ("output.fhir.json", "output.fhir.json", "application/json"),
        "ai_assisted": ("output.ai_assisted.docx", "output.ai_assisted.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        "human_review": ("output.human_review.docx", "output.human_review.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        "protocol": (PROTOCOL_JSON_FILENAME, PROTOCOL_JSON_FILENAME, "application/json"),
    }
    if kind not in allowed:
        abort(404)
    filename, download_name, mimetype = allowed[kind]
    folder = job_folder(job_id)
    path = folder / filename
    if not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name=download_name, mimetype=mimetype)


@app.route("/api/jobs/<job_id>/verification/<kind>")
def verification_report(job_id, kind):
    if kind not in REPORTS:
        abort(404)
    path = job_folder(job_id) / f"output.{kind}.txt"
    if not path.is_file():
        abort(404)
    return app.response_class(path.read_text(encoding="utf-8", errors="replace"), mimetype="text/plain")


@app.route("/api/jobs/<job_id>/usdm")
def usdm_preview(job_id):
    path = job_folder(job_id) / "output.usdm.json"
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="application/json")


@app.route("/api/jobs/<job_id>/fhir")
def fhir_preview(job_id):
    folder = job_folder(job_id)
    path = folder / "output.fhir.json"
    if not path.is_file():
        abort(404)
    return jsonify(json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    import os
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5001")), debug=True)
