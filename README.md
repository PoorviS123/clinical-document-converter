# ClinicalConverter demo

## Run locally

```bash
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -r requirements.txt
python3 app.py
```

Open http://127.0.0.1:5001. The app accepts PDF and DOCX files up to 100 MB.

## Demo behavior

Uploading a file validates its content and creates three **placeholder** outputs: FHIR JSON, AI-assisted V11 DOCX (with one highlighted demo passage), and human-review V11 DOCX. These are not clinical conversions and must not be used clinically.

## Backend

`conversion/__init__.py` runs the `protocol-reformatter` repo's CLI (`python -m converter`) in a subprocess using its own `.venv`. Install it first by following that repo's README (Python 3.12, plus a signed-in `codex` or `claude` CLI for the LLM step).

Demo mode is on by default (`DEMO=1`): the home page offers only the sample protocol in `conversion/demo_input/` (no upload), and the prepared outputs in `conversion/demo_outputs/` are returned without calling the backend. Set `DEMO=0` to run the real conversion.

By default the repo is expected at `../protocol-reformatter`. Settings:

- `PROTOCOL_REFORMATTER_DIR`: path to the backend repo.
- `PROTOCOL_REFORMATTER_LLM`: `cli`, `codex` or `api`. Unset uses the backend default.

Output mapping: AI-assisted V11 is `<name>.docx`, human-review V11 is `<name>.pre-llm.docx`, the FHIR bundle is `<name>.fhir.json`, and the run report `<name>.report.txt` and protocol JSON after AI drafting `<name>.generated.json` are kept when present. The protocol JSON is offered as a download. A conversion can take several minutes; the timeout is 30 minutes. It runs in a background thread, and `/jobs/<id>` shows its progress. The backend prints nothing until it finishes, so each step counts as done once its output file exists.

The results page is a review desk. Its outline and queue come from the FHIR bundle: each Provenance marks an AI-drafted section and each Task is a value withheld for human input. Cross-references to review and omitted optional sections come from the run report, so demo jobs (which have no report) don't show them.

The results page also shows the CeSHarP data read back from the AI-assisted V11 document: the backend's M11 importer turns the DOCX into CDISC USDM JSON (`cesharp_extract.py`), and the page shows its main counts and a browsable tree. If the importer fails, the conversion still succeeds and the section says the data is unavailable.

## Notes

- Uploads and generated outputs are stored locally in `uploads/` and `outputs/`.
- This is a local hackathon demo, not a production system for protected health information.
- DOCX browser previews use the `docx-preview` library from a CDN; downloads remain available if preview rendering is unavailable.
