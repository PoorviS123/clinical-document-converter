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

## Replace the demo converter

The integration point is `conversion/__init__.py`, function `convert_document(input_path, output_dir)`. Replace its implementation with the teammate's agreed converter. Return a dictionary containing paths for `fhir`, `ai_assisted`, and `human_review`. The files must be named `output.fhir.json`, `output.ai_assisted.docx`, and `output.human_review.docx` in the output directory, or update the download routes in `app.py` to match the agreed contract.

## Notes

- Uploads and generated outputs are stored locally in `uploads/` and `outputs/`.
- This is a local hackathon demo, not a production system for protected health information.
- DOCX browser previews use the `docx-preview` library from a CDN; downloads remain available if preview rendering is unavailable.
