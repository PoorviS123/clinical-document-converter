# Backlog

## Structured fields and objectives

The M11 Technical Specification (`resources/reference/ich-m11/`) defines each field's elements, conformance and C-code.

`place.py` fills section content, the title page and front matter only. These stay as the schema's `missing` placeholders:

- The 10.4/10.5 objective analyses (their text lands in additional subsections) and the 1.1.1 synopsis rows, which link to the section 3 objectives by id.
- Terminology: dose form and route are coded from the CDISC SDTM subsets and drug names and conditions from NCI Thesaurus concepts (`system: other`), all via `src/converter/terminology/`. MedDRA, WHO INN and EDQM codes are not available as free downloads, so those slots carry NCIt codes until a source for them is added to `registry.py`.
- 12.3 amendment details: the amendment-change `section` slot (code list too long to offer the model), and the title-page amendment details.

## Smaller gaps

- Cross-reference rewriting is wired into `place_document`. On `2024-519298-21-00_Protocol.pdf`, 232 references are rewritten and `check_output.py` still reports 13 pointing at `8.4.6.1` and `8.4.6.2`. Those sections land as unnumbered additional subsections, so no heading carries the rewritten number.
