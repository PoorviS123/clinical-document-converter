# Sources for the CPT v010 → v011 converter

Collected 2026-11-13. Local files are under `resources/reference/<area>/`. Downloaded files are reference data only; nothing in them is executed.

## 1. TransCelerate CPT

Asset page: https://www.transceleratebiopharmainc.com/assets/clinical-content-reuse-solutions/
Files below are under `https://www.transceleratebiopharmainc.com/wp-content/uploads/<folder>/`. Terms: "AS IS", see https://www.transceleratebiopharmainc.com/terms/ and the CC&R disclaimer on https://www.transceleratebiopharmainc.com/initiatives/clinical-content-reuse/.

| File | Version / folder | Local | Use |
|---|---|---|---|
| `CPT_CoreBWE-v011_M11-Mapping-Reference.docx` | V011, 2026/04 | transcelerate/ | **TransCelerate v10→v11 mapping (known to be slightly inaccurate).** The v11 template with 127 Word comments naming the v10 source of each section. Extracted to `v10-to-v11-mapping.tsv` by `src/extract_mapping_comments.py`. |
| `CTS_ITK-Cumulative-Summary-of-Content-Changes_V004.xlsx` | V004, 2026/03 | transcelerate/ | Per-release change summary including V11 (why sections moved, appendices folded into body). |
| `CTS_ITK-CPT-M11-Alignment-Overview_v001.pptx` | v001, 2026/03 | transcelerate/ | Overview of how CPT V11 aligns to M11. |
| `CTS_lTK-Clinical-Template-Suite-Style-Guide_V008.docx` | V008, 2026/03 | transcelerate/ | Style rules (text conventions, styles) for generated output. |
| `CTS_lTK-CPT-Resources-Prot-Authors_V006.docx` | V006, 2026/03 | transcelerate/ | Author guidance. |
| `CTS_ITK-CTS-FAQs_V011.xlsx` | V011, 2026/03 | transcelerate/ | FAQs. |
| `CPT_CoreTEE_v010.docx` | v010, 2024/01 | transcelerate/ | Technology-Enabled Edition of v010. Has tagged variables; useful if inputs were authored with the eCPT add-in. No V11 TEE will be released. |
| `CTS_ITK-eTemplates-Variable-Reuse-Map_V004.xlsx` | V004, 2024/01 | transcelerate/ | eCPT variable names and where they are reused. |
| `Libraries.zip` (TA and participant-type libraries, revised to V11, files modified 2026-02-25) | 2026/04 | transcelerate/libraries/ (downloaded by hand; scripted download gets HTTP 403) | Section text that plugs into the v011 backbone, numbered to v011. Patient and Healthy Volunteer: 5.4 Contraception, 7.1 and 12.4 liver safety, 8.4.5 pregnancy testing. Also Pediatrics (V008), Pregnant Participants (V002), Liver Safety in Oncology (V002), Breast Cancer, Prostate Cancer, Vaccines (V006). |

## 2. ICH M11 (CeSHarP), Step 4, adopted 19 Nov 2025

Landing page: https://ich.org/page/multidisciplinary-guidelines#11 (related files: https://ich.org/page/m11-clinical-electronic-structured-harmonised-protocol-cesharp-related-files)

| File | URL | Local | Use |
|---|---|---|---|
| Guideline | https://database.ich.org/sites/default/files/ICH_Step4_M11_Final_Guideline_2025_1119.pdf | ich-m11/ | Normative scope. |
| Template | https://database.ich.org/sites/default/files/ICH_Step4_M11_Final_Template_2025_1119.pdf | ich-m11/ | Section structure v011 follows. |
| Technical Specification | https://database.ich.org/sites/default/files/ICH_Step4_M11_Final_TechnicalSpecification_2025_1119.pdf | ich-m11/ | Data elements, cardinality, required/optional, codelist bindings, OIDs. Main input for structured output. |
| Explainer (May 2026) | https://database.ich.org/sites/default/files/ICH%20M11%20Explainer_May2026.pdf | ich-m11/ | Short overview; PDF metadata still says "DRAFT". |

Regional copies: EMA Step 5 template and tech spec (effective 11 June 2026) at ema.europa.eu; FDA guidance page https://www.fda.gov/regulatory-information/search-fda-guidance-documents/m11-template-clinical-electronic-structured-harmonised-protocol.

## 3. ICH M11 Controlled Terminology (NCI EVS), release 2025-12-19

Directory: https://evs.nci.nih.gov/ftp1/ICH/M11/ (archive in `Archive/`). Change requests via the CDISC form linked in `ReadMe.txt`.

Local in `nci-evs-m11/`: `ReadMe.txt`, `ICH M11 Terminology.txt`, `.xls`, `.odm.xml`, `.pdf`, `ICH M11 Terminology Changes.txt`. There is no `.xlsx` on the server; use `.xls` or `.txt`.

Lookup of single NCIt concepts outside the M11 subset: `https://api-evsrest.nci.nih.gov/api/v1/concept/ncit/<code>`.

## 4. CDISC USDM / DDF

Repo: https://github.com/cdisc-org/DDF-RA. Tag `v4.0.0` released 2025-06-03 (latest tag). Code MIT, content CC-BY-4.0.

| File | Source | Local | Use |
|---|---|---|---|
| `USDM_API.json` | `Deliverables/API/` @ v4.0.0 | cdisc-usdm/ | OpenAPI schema for USDM JSON output. |
| `USDM_CT.xlsx` | `Deliverables/CT/` @ v4.0.0 | cdisc-usdm/ | USDM controlled terminology. |
| `USDM-IG.pdf` | `Deliverables/IG/` @ v4.0.0 | cdisc-usdm/ | Implementation guide. |
| `USDM_CORE_Rules.xlsx` | `Deliverables/RULES/` @ v4.0.0 | cdisc-usdm/ | Conformance rules to validate output. |
| `m11_mapping.xlsx` | `Documents/Mappings/` @ main | cdisc-usdm/ | CDISC's M11→USDM mapping. Built against **M11 Step 2 draft (14 Mar 2025)**; older than our local sheet. |
| `cpt_mapping.xlsx` | `Documents/Mappings/` @ main | cdisc-usdm/ | CPT→USDM mapping. Check which CPT version it targets before relying on it. |

Python package: `usdm` on PyPI (0.67.0), https://pypi.org/project/usdm/: USDM model classes for building and validating JSON.

Local `resources/M11 to FHIR Mapping 02.xlsx`: M11 Step 4 (19 Nov 2025) → USDM v4.0.0 ("update pending"). Despite the name, its paths target USDM, not FHIR. Use this as the primary element mapping.

## 5. HL7 FHIR

| Item | URL | Local | Notes |
|---|---|---|---|
| Clinical Study Protocol IG, 1.0.0-ballot2 (May 2026) | https://hl7.org/fhir/uv/clinical-study-protocol/2026May/en/index.html | hl7-fhir/package.tgz | FHIR R6 (6.0.0-ballot3), CC0. Not yet published as a release. |
| CI build | https://build.fhir.org/ig/HL7/vulcan-udp-ig/en/ | — | Changes often; use for latest only. |
| M11 ResearchStudy profile mappings | https://hl7.org/fhir/uv/clinical-study-protocol/2026May/en/StructureDefinition-m11-research-study-profile-mappings.html | — | M11 element → FHIR path. |
| Earlier ballot (Pharmaceutical Research Protocol, Sep 2025) | https://hl7.org/fhir/uv/pharmaceutical-research-protocol/2025Sep | — | Superseded name. |

Related: data4knowledge M11/USDM demonstrator resources, https://github.com/data4knowledge/usdm_m11_resources.

## 6. Word/docx tooling

| Item | URL | Notes |
|---|---|---|
| python-docx 1.2.0 | https://python-docx.readthedocs.io/ | Styles, paragraphs, tables. Comments, hidden text (`w:vanish`) and content controls (`w:sdt`) need direct XML access via `element`. |
| docxcompose 2.2.0 (MIT) | https://pypi.org/project/docxcompose/ | Appending documents with style and numbering remapping. |
| ECMA-376 (OOXML) | https://ecma-international.org/publications-and-standards/standards/ecma-376/ | Part 1: §17.7 styles, §17.9 numbering, §17.13 annotations (comments, revisions). |

Style mapping between templates: v010 uses `Heading1`–`Heading4`; v011 uses `01Heading1`–`04Heading4`, `CPTInstructional` (red hidden text), `CPTExample`, `00Paragraph`, `08SubheadingBold`.
