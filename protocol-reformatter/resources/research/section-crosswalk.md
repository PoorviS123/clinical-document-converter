# CPT v010 → v011 section crosswalk

Starting point: TransCelerate `CPT_CoreBWE-v011_M11-Mapping-Reference.docx` (V011, April 2026). Its mappings are known to be slightly inaccurate, so treat each row as a candidate to verify against the v010/v011 template text and the M11 Step 4 Template instructions, not as ground truth. Its comments are extracted to [`v10-to-v11-mapping.tsv`](v10-to-v11-mapping.tsv); regenerate with:

```
python3 src/extract_mapping_comments.py resources/reference/transcelerate/CPT_CoreBWE-v011_M11-Mapping-Reference.docx > resources/research/v10-to-v11-mapping.tsv
```

Rows marked *inferred* are not in the official mapping; they match by heading name or content. Section numbers come from the heading order in each template.

Change types: **rename** (same content, new title), **move** (new location), **split** (one v010 section feeds several v011 sections), **merge** (several v010 sections feed one), **library** (content now ships in a CPT library, not the core template).

## Title page and front matter

| v010 | v011 | Type |
|---|---|---|
| Protocol Title | Full Title (built from M11 CT fields: intervention model, primary purpose, phase, blinding, …) | rename + restructure |
| Acronym | Trial Acronym | rename |
| Protocol Number | Sponsor Protocol Identifier | rename |
| Compound | Investigational Product Code(s) | rename |
| (intervention section only) | Investigational Product Name(s) | new field |
| Study Phase | Trial Phase (M11 CT) | rename |
| Brief Title | Short Title | rename |
| Sponsor Name + Legal Registered Address | Sponsor Name and Address | merge |
| Manufacturer | Device Manufacturer Name and Legal Address | rename |
| Regulatory Agency Identifier Number(s), Pediatric Investigational Plan Number | Regulatory or Clinical Trial Identifier(s) (table) | merge |
| Approval Date | Sponsor Approval | rename |
| Sponsor Signatory | Sponsor Signatory | same |
| Medical Monitor Name and Contact Information | Medical Expert Contact | rename |
| Amendment Scope, Country/Region Identifier | Amendment Scope (Global / Not Global + identifier) | merge |
| Protocol Amendment Summary of Changes Table | Amendment Details | rename + restructure |
| List of Abbreviations [and Definitions of Terms] | 13 Appendix: Glossary of Terms and Abbreviations | move |

## Body

| v010 | v011 | Type |
|---|---|---|
| 1 Protocol Summary | 1 Protocol Summary | same |
| 1.1 Synopsis | 1.1 Protocol Synopsis | rename; title, brief title, identifiers and rationale no longer repeated here |
| 1.1 Synopsis: Objectives, Endpoints, Estimands | 1.1.1 Primary and Secondary Objectives and Estimands | move |
| 1.1 Synopsis: Overall Design, Brief Summary, Number of Participants, Arms and Duration, DMC | 1.1.2 Overall Design (Brief Summary → Duration field) | merge, reorganize |
| 1.2 Schema | 1.2 Trial Schema | rename |
| 1.3 Schedule of Activities (SoA) | 1.3 Schedule of Activities | rename |
| 2 Introduction | 2 Introduction | same |
| 2.1 Study Rationale | 2.1.1 Trial Rationale (under new 2.1 Purpose of Trial) | move |
| 2.2 Background | 2.1.2 Background | move |
| 2.3 Benefit/Risk Assessment (+2.3.1, 2.3.2) | 2.2 Assessment of Risks and Benefits | rename |
| 2.3.3 Overall Benefit Risk Conclusion | 2.2.3 Overall Risk-Benefit Assessment | rename |
| 3 Objectives, Endpoints, and Estimands | 3 Trial Objectives and Associated Estimands | rename |
| 3 Primary objectives/estimands | 3.1 Primary Objective(s) and Associated Estimand(s) | move to L2 |
| 3 Secondary objectives/estimands | 3.2 Secondary Objective(s) and Associated Estimand(s) | move to L2 |
| 3 [Tertiary/Exploratory/Other] objectives | 3.3 Exploratory Objective(s) | move to L2 |
| 4 Study Design | 4 Trial Design | rename |
| 4.1 Overall Design | 4.1 Description of Trial Design | rename |
| 4.2 Scientific Rationale for Study Design | 4.2 Rationale for Trial Design | rename |
| 4.2.1 Patient Input into Design | 4.1.1 Stakeholder Input into Design | move |
| 4.3 Justification for Dose | 4.2.2 Rationale for Intervention Model (official); see open question 1 | move |
| 4.4 End-of-Study Definition | 4.4 Start of Trial and End of Trial | merge (with 10.1.10) |
| 5 Study Population | 5 Trial Population; L1 general text goes to 5.1 | rename |
| 5.1 Inclusion Criteria | 5.2 Inclusion Criteria | move |
| 5.2 Exclusion Criteria | 5.3 Exclusion Criteria | move |
| 5.3 Lifestyle Considerations (5.3.1–5.3.4) | 5.5 Lifestyle Restrictions (5.5.1–5.5.4) | move, L3s renamed |
| 5.4 Screen Failures | 5.6 Screen Failure and Rescreening | move |
| 5.5 Criteria for Temporarily Delaying … | 5.7 same title (*inferred*) | move |
| 6 Study Intervention(s) and Concomitant Therapy | 6 Trial Intervention and Concomitant Therapy | rename |
| 6.1 Study Intervention(s) Administered | 6.1 Description of Investigational Trial Intervention and 6.9 Description of Noninvestigational Trial Intervention | split |
| 6.1.1 Rescue Medicine | 6.9.2 Rescue Therapy | move |
| 6.1.2 Medical Devices | 6.1.1 Medical Devices | move |
| 6.2 Preparation, Handling, Storage, and Accountability | 6.6.1–6.6.3 (Preparation / Storage and Handling / Accountability); no text at 6.6 | split |
| 6.3 Assignment to Study Intervention | 6.7.1 Participant Assignment, 6.7.2 Randomization | split |
| 6.4 [Blinding, Masking] | 6.7.3 Measures to Maintain Blinding | move |
| 6.5 Study Intervention Compliance | 6.8 Investigational Trial Intervention Adherence | rename |
| 6.6 Dose Modification (+6.6.1 Retreatment Criteria) | 6.4 Dose Modification (6.3.1 Retreatment Criteria in v011 outline) | move |
| 6.7 Continued Access … after End of Study | 4.5 Access to Trial Intervention After End of Trial | move |
| 6.8 Treatment of Overdose | 6.5 Management of Investigational Trial Intervention Overdose | move |
| 6.9 Prior and Concomitant Therapy | 6.10 Concomitant Therapy (6.10.1 Prohibited, 6.10.2 Permitted) | move |
| 7 Discontinuation of Study Intervention and Participant Discontinuation/Withdrawal | 7 Participant Discontinuation … (*inferred*) | rename |
| 7.1 Discontinuation of Study Intervention (+7.1.1 Liver, 7.1.2 QTc) | 7.1 Discontinuation of Trial Intervention for Individual Participants (7.1.1.x) | rename |
| 7.1.3 Temporary Discontinuation | 7.1.2 Temporary Discontinuation of Trial Intervention | move |
| 7.1.4 Rechallenge (+7.1.4.1) | 7.1.3 Rechallenge (+7.1.3.1) | move |
| 7.2 Participant Discontinuation/Withdrawal | 7.2 Participant Discontinuation or Withdrawal from the Trial | rename |
| 7.3 Lost to Follow up | 7.3 Management of Loss to Follow-Up | rename |
| 8 Study Assessments and Procedures | 8 Trial Assessments and Procedures; L1 text goes to 8.1 | rename |
| 8.1 Administrative [and General/Baseline] Procedures | 8.2 Screening/Baseline Assessments and Procedures | move |
| 8.2 [Efficacy and/or Immunogenicity] Assessments | 8.3 Efficacy Assessments and Procedures | move |
| 8.3 Safety Assessments (8.3.1–8.3.6) | 8.4 Safety Assessments and Procedures (8.4.1–8.4.6) | move |
| 8.4 AEs, SAEs, Other Safety Reporting | 9 Adverse Events, … Special Safety Situations (new L1) | move, see Section 9 below |
| 8.5 Pharmacokinetics | 8.5 Pharmacokinetics | same |
| 8.6 Pharmacodynamics | 8.6.2 Pharmacodynamic Biomarkers | move |
| 8.7 Genetics (+ Appendix 10.5 Genetics) | 8.6.1 Genetics, Genomics, Pharmacogenetics and Pharmacogenomics | merge |
| 8.8 Biomarkers | 8.6 Biomarkers (no text at L2 per M11) | move |
| 8.9 Immunogenicity Assessments | 8.7 Immunogenicity Assessments | move |
| 8.10 Health Economics / MRU | 8.8 Medical Resource Utilization and Health Economics | move |
| 9 Statistical Considerations | 10 Statistical Considerations | move |
| 9.1 General Considerations, 9.1.1 Decision Criteria/Hypotheses, 9.1.3 Intercurrent Events | 10.1 General Considerations | merge |
| 9.1.2 Multiplicity Adjustment | 10.10 Multiplicity Adjustments | move |
| 9.1.4 Handling of Missing Data | 10.4.1.3 (and 10.5.1.3) Handling of Missing Data per estimand | split |
| 9.2 Analysis Sets | 10.2 Analysis Sets | move |
| 9.3 Analyses Supporting Primary Objective(s) (9.3.1–9.3.1.2) | 10.4 Analyses Associated with the Primary Objective(s) | move |
| 9.3.1.3 Sensitivity | 10.4.1.4 Sensitivity Analysis | move |
| 9.3.1.4 Supplementary | 10.4.1.5 Supplementary Analysis | move |
| 9.4 Analyses Supporting Secondary Objective(s) | 10.5 (content must be re-fit to 10.5.1.x L4 structure) | move + restructure |
| 9.5 Analyses Supporting [Tertiary/Exploratory/Other] | 10.6 Analyses Associated with the Exploratory Objective(s) | move |
| 9.6 [Other] Safety Analyses | 10.7 Safety Analyses | move |
| 9.7 Other Analyses (9.7.1, 9.7.2) | 10.8 Other Analyses (10.8.1, 10.8.2) | move |
| 9.8 Interim Analyses | 10.9 Interim Analyses | move |
| 9.9 Sample Size Determination | 10.11 Sample Size Determination | move |
| 10 Supporting Documentation and Operational Considerations | 11 Trial Oversight and Other General Considerations | rename |
| 10.1 Appendix 1 (+10.1.1 Regulatory and Ethical, 10.1.2 Financial Disclosure) | 11.1 Regulatory and Ethical Considerations | merge, appendix → body |
| 10.1.3 Informed Consent Process | 11.3 Informed Consent Process | move |
| 10.1.4 Recruitment strategy | 5.8 [Recruitment Strategy] (*inferred*) | move |
| 10.1.5 Data Protection, 10.1.7 Dissemination, 10.1.11 Publication | 11.7 Data Governance (official); see open question 2 | merge |
| 10.1.6 Committees Structure (+10.1.6.1) | 11.4 Committees | move |
| 10.1.8 Data Quality Assurance | 11.9.1 Data Quality Assurance (*inferred*) | move |
| 10.1.9 Source Documents | 11.9.2 Source Documents (*inferred*) | move |
| 10.1.10 Study and Site Start and Closure | 4.4 Start of Trial and End of Trial and 11.11 Early Site Closure | split |
| 10.1.11 Publication Policy | 11.12.1 Publication Policy | move |
| 10.2 Appendix 2: Clinical Laboratory Tests | 12.1 Clinical Laboratory Tests | move |
| 10.3 Appendix 3: AE/SAE definitions and procedures | 9 (see below) | move |
| 10.4 Appendix 4: Contraceptive and Barrier Guidance | 5.4 Contraception (5.4.1 Definitions, 5.4.2 Requirements); text in libraries | move + library |
| 10.5 Appendix 5: Genetics | 8.6.1 | merge |
| 10.6 Appendix 6: Liver Safety | 12.4 Liver Safety; also 7.1 guidance; text in libraries | move + library |
| 10.7 Appendix 7: Medical Device AEs … | 9.1.4, 9.2.x (see below) | move |
| 10.8 Appendix 8: Country-specific Requirements | 12.2 Country/Region-Specific Differences | move |
| 10.9 Appendix 9: Protocol Amendment History | 12.3 Prior Protocol Amendment(s) | move |
| 11 References | 14 Appendix: References | move |

### New v011 Section 9 (safety reporting)

| v010 | v011 |
|---|---|
| 10.3.1 Definition of AE | 9.1.1 Definitions of Adverse Events |
| 10.3.2 Definition of SAE | 9.1.2 Definitions of Serious Adverse Events |
| 10.7.1, 10.7.2 Medical device AE/ADE, SAE/SADE/USADE | 9.1.4 Definitions of Medical Device Product Complaints |
| 10.7.3 Definition of Device Deficiency | not mapped; likely 9.1.4 (open question 3) |
| 8.4.1 Time Period and Frequency; 8.4.9.1 Device Deficiency time period | 9.2 / 9.2.1 Timing |
| 10.3.3 Recording and Follow-Up; 10.7.4 device recording | 9.2.2 Collection Procedures |
| 8.4.2 Method of Detecting AEs/SAEs | 9.2.2 "Identification" paragraph |
| 10.3.3 | 9.2.2 "Severity" paragraph |
| 8.4.3, 10.3.3, 10.7.4 | 9.2.2 "Causality" and "Follow-up" paragraphs (+8.4.9.2) |
| 10.3.4 Reporting of SAEs; 10.7.5; 8.4.9.3 | 9.2.3 Reporting |
| 8.4.4 Regulatory Reporting; 8.4.9, 8.4.9.4; 10.7.6 SADEs | 9.2.3.1 Regulatory Reporting Requirements |
| 8.4.8 AESIs | 9.2.4 Adverse Events of Special Interest |
| 8.4.7 Disease-related Events/Outcomes | 9.2.5 Disease-Related Events or Outcomes Not Qualifying as AEs or SAEs |
| 8.4.5 Pregnancy | 9.3.1 Participants Who Become Pregnant During the Trial |
| 8.4.6 Cardiovascular and Death Events | not mapped (open question 3) |

## v011 sections with no v010 source

These need new content or stay empty with "Not applicable" (v011 rule: L1/L2 headings are never deleted).

2.1 Purpose of Trial (container), 2.2.1 Risk Summary and Mitigation Strategy, 2.2.2 Benefit Summary, 4.2.1 Rationale for Estimand(s), 4.2.3–4.2.7 (Control Type, Trial Duration, Adaptive/Novel Design, Interim Analysis, Other), 4.3 Trial Stopping Rules, 5.1 Description of Trial Population and Rationale, 6.2 Rationale for Dose and Regimen (see open question 1), 6.3 Investigational Trial Intervention Administration, 6.7.4 Emergency Unblinding at the Site, 6.9.1 Background Trial Intervention, 6.9.3 Other Noninvestigational Trial Intervention, 7.1.1 Permanent Discontinuation, 8.1 Trial Assessments and Procedures Considerations, 8.6.3 Other Biomarkers, 9.1.3 Definitions of Product Complaints, 9.3.2 Partners Who Become Pregnant, 9.4 Special Safety Situations, 10.3 Analyses of Demographics and Baseline Variables, 11.2 Trial Oversight (11.2.1 Investigator, 11.2.2 Sponsor Responsibilities), 11.3.1–11.3.2 Informed Consent for Rescreening / Remaining Samples, 11.5 Insurance and Indemnity, 11.6 Risk-Based Quality Management, 11.8 Data Protection, 11.10 Protocol Deviations, 11.12 Data Dissemination.
