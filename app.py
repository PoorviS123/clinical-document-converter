from pathlib import Path
from uuid import uuid4
import json
import zipfile
import re
import shutil
import threading

from flask import Flask, jsonify, render_template, request, send_file, abort
from pypdf import PdfReader
from werkzeug.utils import secure_filename

from conversion import convert_document
from document_preview import render_docx_preview

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
MAX_BYTES = 100 * 1024 * 1024
ALLOWED = {".pdf", ".docx"}

app = Flask(__name__)
jobs = {}
app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES

def run_conversion(job_id: str, input_path: Path, job_output_dir: Path, safe_name: str):
    """Run conversion in the background and update the job status."""
    try:
        jobs[job_id]["status"] = "processing"
        jobs[job_id]["message"] = "Generating your three output files..."

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

        json.loads(expected["fhir"].read_text(encoding="utf-8"))

        for key in ("ai_assisted", "human_review"):
            with zipfile.ZipFile(expected[key]) as archive:
                if "word/document.xml" not in archive.namelist() or archive.testzip():
                    raise RuntimeError(f"Converter produced an invalid {key} DOCX.")

        manifest = {
            "job_id": job_id,
            "original_filename": safe_name,
            "demo": True,
            "outputs": {key: Path(value).name for key, value in outputs.items()}
        }

        (job_output_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

        jobs[job_id]["status"] = "completed"
        jobs[job_id]["message"] = "All three outputs are ready."
        jobs[job_id]["results_url"] = f"/results/{job_id}"

    except Exception:
        app.logger.exception("Conversion failed for job %s", job_id)
        shutil.rmtree(job_output_dir, ignore_errors=True)
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["message"] = "Conversion failed. Please try another file."


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
    return render_template("index.html")


@app.route("/api/convert", methods=["POST"])
def upload_and_convert():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify(error="Choose a PDF or DOCX file first."), 400

    raw_name = uploaded.filename
    extension = Path(raw_name).suffix.lower()
    if extension not in ALLOWED:
        return jsonify(error="Unsupported file type. Please upload a PDF or DOCX."), 400

    safe_name = secure_filename(raw_name) or f"document{extension}"
    job_id = uuid4().hex
    job_upload_dir = UPLOAD_DIR / job_id
    job_output_dir = OUTPUT_DIR / job_id
    job_upload_dir.mkdir(parents=True, exist_ok=True)
    job_output_dir.mkdir(parents=True, exist_ok=True)
    input_path = job_upload_dir / f"source{extension}"
    uploaded.save(input_path)

    try:
        validate_content(input_path, extension)
    except ValueError as exc:
        shutil.rmtree(job_upload_dir, ignore_errors=True)
        shutil.rmtree(job_output_dir, ignore_errors=True)
        return jsonify(error=str(exc)), 400

    jobs[job_id] = {
        "status": "processing",
        "message": "Your document is queued for conversion."
    }

    worker = threading.Thread(
        target=run_conversion,
        args=(job_id, input_path, job_output_dir, safe_name),
        daemon=True
    )
    worker.start()

    return jsonify(success=True, job_id=job_id), 202

@app.route("/api/jobs/<job_id>/status")
def job_status(job_id):
    job_folder = jobs.get(job_id)

    if job_folder is None:
        return jsonify(error="Job not found."), 404

    return jsonify(job_folder)





@app.route("/results/<job_id>")
def results(job_id):
    folder = job_folder(job_id)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    return render_template("results.html", job=manifest)


@app.route("/files/<job_id>/<kind>")
def download_output(job_id, kind):
    allowed = {
        "fhir": ("output.fhir.json", "output.fhir.json", "application/json"),
        "ai_assisted": ("output.ai_assisted.docx", "output.ai_assisted.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        "human_review": ("output.human_review.docx", "output.human_review.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    }
    if kind not in allowed:
        abort(404)
    filename, download_name, mimetype = allowed[kind]
    folder = job_folder(job_id)
    path = folder / filename
    if not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name=download_name, mimetype=mimetype)


@app.route("/preview/<job_id>/<kind>")
def preview_docx(job_id, kind):
    names = {"ai_assisted": "output.ai_assisted.docx", "human_review": "output.human_review.docx"}
    if kind not in names:
        abort(404)
    folder = job_folder(job_id)
    path = folder / names[kind]
    if not path.is_file():
        abort(404)
    try:
        return app.response_class(render_docx_preview(path), mimetype="text/html")
    except Exception:
        app.logger.exception("DOCX preview rendering failed for job %s", job_id)
        return jsonify(error="The DOCX preview could not be generated. Please download the document instead."), 500


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
