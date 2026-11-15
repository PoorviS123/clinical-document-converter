# Protocol reformatter

Convert TransCelerate CPT v010 protocol PDFs or Word files into CPT v011 Word documents. Sections that v011 requires but v010 has no source for are drafted by an LLM from the protocol's own text and highlighted for review. Mapping uses `resources/research/crosswalk.tsv`.

## Install

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt
```

The default LLM backend runs the local `claude` CLI, so it needs Claude Code installed and signed in. `--llm codex` runs the local `codex` CLI instead (Codex installed and signed in). For the API backend, also run `uv pip install anthropic` and set `ANTHROPIC_API_KEY`.

## Convert

```sh
cd src
../.venv/bin/python -m converter ../resources/example-protocols/v010/<name>.pdf --out-dir ../output
```

Options:

- `--llm cli|codex|api` picks the backend (default `cli`); `--model` overrides its model.
- `--skip-generate` stops after the pre-LLM document and makes no LLM calls.
- `--template` and `--crosswalk` override the defaults.

Per input, in `--out-dir`:

| File | What it is |
|---|---|
| `<name>.json` | Protocol JSON (`schema/protocol.schema.json`), validated before anything else is written |
| `<name>.layout.json`, `<name>.assets/` | Layout sidecar: formatting, merged-cell tables, figures. Keep them with the JSON |
| `<name>.pre-llm.docx` | v011 document before any LLM content |
| `<name>.generated.json` | Protocol JSON after LLM drafting; drafted blocks have origin `ai_generated` |
| `<name>.docx` | v011 document with LLM-drafted sections highlighted yellow |
| `<name>.report.txt` | Unmatched sections and the LLM step's result |

Each draft prompt carries the ICH M11 specification's user guidance for that section (`resources/research/m11_user_guidance.json`) and, for a few rationale sections, a short style excerpt from a published v011 protocol. The LLM step only drafts sections the source gives a basis for. Sections it can't support stay red `[NOT EXTRACTED]`. The report lists which sections were drafted, which had no basis, and which failed. LLM calls then read structured fields from the protocol text: the 1.1.2 Overall Design values, the section 6 interventions table, the 9.2 safety event reporting table, 12.2 country requirements (only when 12.2 is empty), 12.3 amendment history and the section 3 objectives with their estimands. Each value is highlighted and flagged for review, and answers that don't fit the schema are dropped. Where objectives are extracted, the source text they replace is removed from section 3. Dose form and route stay as the source's own words because the schema wants EDQM codes the converter can't look up. In 5.2 and 5.3, numbered criteria are renumbered as one flat list and sponsor category subheadings are dropped (the report lists them). Sections the M11 specification marks optional or conditional that are still empty after drafting are left out of `<name>.docx` and listed in the report.

Input is a PDF or a Word (.docx) file. In a .docx, tracked insertions are kept and tracked deletions are dropped. In a PDF, scanned pages stay images; there is no OCR. In Word, update the table of contents to populate page numbers.

## Test

```sh
.venv/bin/python -m unittest discover -s tests -v
cd schema && ../.venv/bin/python test_protocol_schema.py
```
