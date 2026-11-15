# ClinicalConverter

Flask wrapper for the protocol reformatter. The web app accepts PDF or DOCX uploads, runs conversion in a background job, and shows downloadable human-review and AI-assisted v011 DOCX files with independent previews. The FHIR card is visible but disabled while FHIR generation is developed separately.

## Setup

The teammate backend requires **Python 3.12** (`usdm4_protocol` does not support Python 3.13+). Install the Codex CLI and sign in with your ChatGPT account so the `codex` command works in the same shell used to start Flask. The backend uses its local `codex exec` provider; it does not require Claude CLI or an Anthropic API key.

From the project directory:

```bash
python3.12 -m venv .venv312
source .venv312/bin/activate
python -m pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5001. The exact app command after setup is `python app.py` with the Python 3.12 virtual environment activated (or `.venv312/bin/python app.py`). The existing `.venv` is not used or modified.

The backend is kept separately in `protocol-reformatter/`; its templates, schemas, crosswalks, and other resources remain in that folder. The Flask adapter invokes the backend's actual CLI with `--llm codex` when `codex` is available on the app's PATH. Otherwise it passes `--skip-generate`. If Codex is unavailable or every draft attempt fails, Flask serves the real human-review DOCX and marks the AI card and preview unavailable.

## Output mapping

- `<input-name>.pre-llm.docx` → `output.human_review.docx`
- `<input-name>.docx` → `output.ai_assisted.docx`

The backend's JSON files are its internal protocol representation and are not FHIR. No FHIR file is generated or served by this integration.

## Notes

- Uploads and generated files are stored locally in `uploads/` and `outputs/`.
- Claude CLI runs locally as the current user; no API key is read or stored by this app.
- Scanned PDF pages remain images in the backend; it does not perform OCR.
- This local prototype is not a production system for protected health information.
