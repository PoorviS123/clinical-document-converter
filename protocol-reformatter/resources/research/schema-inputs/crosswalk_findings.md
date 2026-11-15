# CPT v010 -> v011 crosswalk verification: findings

Method: every v10 and v11 heading (all levels) plus the title-page and synopsis fields were dumped from the two templates with tracked changes accepted (`visible_text` logic; neither template contains tracked changes). Each mapping was decided by comparing the instruction text and common/example text of the two templates (most matches are verbatim after study -> trial). The TransCelerate comments (`v10-to-v11-mapping.tsv`), the ICH M11 Step 4 template (pypdf text), the Cumulative Summary of Content Changes (V11 rows) and the V010 Patient/Healthy Volunteer libraries were used as cross-checks.

Result: `crosswalk_verified.json` has 184 rows (every v10 numbered heading, 11 unnumbered subheadings, 20 title-page/front-matter rows, 11 synopsis-field rows). Confidence: 139 official-confirmed, 26 official-corrected, 17 inferred-strong, 2 inferred-weak. 30 corrections to `section-crosswalk.md` are listed below (several are also corrections to the TransCelerate file).

## 1. Corrections vs section-crosswalk.md

Wrong target (text comparison contradicts md and/or TransCelerate):

| v10 | md / TransCelerate | Verified | Evidence |
|---|---|---|---|
| 4.3 Justification for Dose | 4.2.2 (official) | **6.2** (dose rationale); the dose-escalation "will be temporarily halted" example text -> **4.3 Trial Stopping Rules** | v10 4.3 "Provide justification for the selection of the doses of all study interventions... first-time in human... starting dose" is verbatim v11 6.2; v11 4.2.2 only says "with a cross reference to Section 6.2" |
| 10.1.5 Data Protection | 11.7 | **11.8** | Unique-identifier common text and "measures to be taken in case of a data security breach" verbatim in v11 11.8 |
| 10.1.7 Dissemination of Clinical Study Data | 11.7 (merge with 10.1.5, 10.1.11) | **11.12 Data Dissemination** | "Disclosure of CSRs... posting on clinicaltrials.gov... Publication planning" bullets verbatim in v11 11.12. TransCelerate's "10.1.11 Publication Strategy" is one of these bullets, not a heading |
| 10.1.2 Financial Disclosure | 11.1 | **11.2.1 Investigator Responsibilities** | "Include text related to financial disclosure if not included in another document" is verbatim v11 11.2.1 |
| 10.1.1 Regulatory and Ethical | 11.1 | 11.1 **plus 11.2.1** | last bullet group "The investigator will be responsible for the following" is v11 11.2.1 example text |
| 10.1.8 Data Quality Assurance | 11.9.1 | 11.9.1 **plus 11.7** (record-retention bullet) | "Records and documents, including signed ICFs... retained for [X] years" is under v11 11.7 |
| 10.1.3 Informed Consent | 11.3 | 11.3 **plus 11.3.1, 11.3.2** | rescreening-ICF and remaining-samples instructions are the v11 L3 instructions |
| 7.1 | 7.1 | **7.1.1** (v11 7.1 is heading-only); 7.1.1/7.1.2 -> 7.1.1.1/7.1.1.2 | v11 7.1: "No text is intended here"; criteria text is in 7.1.1 |
| 6.4 Blinding | 6.7.3 | 6.7.3 **plus 6.7.4** | "circumstances in which the blind will be broken" + blind-break examples are in v11 6.7.4 |
| 6.6.1 Retreatment Criteria | 6.4 (TransCelerate) | **6.3.1** | "may be required in certain types of studies, eg, medical aesthetics" verbatim v11 6.3.1 |
| 8.8 Biomarkers | 8.6 (heading, no text) | **8.6.3 Other Biomarkers** | retention time / types of biomarkers instructions verbatim |
| 8.4 intro | 9 | 9.2 intro + 9.2.2 first paragraph | "The means of obtaining AE data should be described..." verbatim v11 9.2 |
| 8.4.3 Follow-up | Causality + Follow-up | Follow-up only | first bullet of v11 9.2.2 Follow-up |
| 8.4.9 Medical Device Deficiencies | 9.2.3.1 | **9.1.4** (intro paragraphs) | "This section is required for a trial in which a medical device is provided" is in v11 9.1.4 |
| 8.4.9.1 | 9.2 / 9.2.1 | **9.2.1.1** | v11 has this L4, text verbatim |
| 10.7.6 Reporting of SADEs | 9.2.3.1 | **9.2.3** | "SADE Reporting to [X]" sits under 9.2.3 before the L4 9.2.3.1 |
| 10.3.3 | Severity, Causality, Follow-up | also **Recording** | v10 "AE and SAE Recording" block = v11 9.2.2 Recording |
| 10.3 (container) | 9 | 9.1 (+9.1.4 cross-ref note) | children go to 9.1.1, 9.1.2, 9.2.2, 9.2.3 |
| 9.1.1 Decision Criteria/Hypotheses | 10.1 | **10.4.1.1 + 10.10** | v11 10.4.1.1 "state the null and alternative hypotheses... non-inferiority margin should be justified"; v11 10.10 holds the hypothesis examples. v11 10.1 has no hypothesis text |
| 9.1.3 Intercurrent Events | 10.1 | **10.4.1.2** (+10.5.1.2) | "Describe how intercurrent events will be summarized (number by treatment condition and timing)" verbatim 10.4.1.2 |
| 9.1.4 Missing Data | 10.4.1.3 (+10.5.1.3) | also 10.1 | v11 10.1 bullet "Handling of missing baseline measurements" |
| 9.3.1.1 / 9.3.1.2 | 10.4 | **10.4.1.1** | "Definition of endpoint(s)" + analysis method are 10.4.1.1 |
| 2.3.1 / 2.3.2 | 2.2 | **2.2.1 / 2.2.2** | verbatim; md wrongly lists 2.2.1 and 2.2.2 as having no v10 source |
| 1.1 Brief Summary | 1.1.2 Duration (wholesale) | drop; only study/treatment duration values -> 1.1.2 Duration | Cumulative Change Summary V11 s.1: "Removed CPT Brief Summary structured text" |
| 6 intro / 6.1 | content "under M11 6.2" | v10 6 intro stays at v11 6; v10 6.1 table -> v11 6 intro table (+6.1, +6.9) | v11 6 intro carries the table and the IMP/NIMP text |

Gaps in md/TransCelerate that are now filled: title-page **Amendment Number -> [Amendment Identifier]**, **PIP Number -> identifier table** ("Add fields for 'other'... Pediatric Investigational Plan Number"); 10.7.3 (see Q3); 8.4.6 (see Q3); 10.1.4 -> 5.8, 10.1.8/10.1.9, 5.5 -> 5.7 upgraded from "inferred" to inferred-strong (verbatim instruction text).

TransCelerate entries confirmed correct (text-verified) include nearly all title-page, section 3-6, 8, 9 -> 10, 10.2/10.4/10.5/10.8/10.9/11 rows. Not re-verified beyond heading + instruction match: 1.2, 1.3 (heading-only in v10), 8.5.

## 2. Open questions

**Q1. v10 4.3 Justification for Dose: 6.2 (not 4.2.2).** Text is verbatim v11 6.2 (dose/route rationale, FTiH starting dose, dose escalation, device "term dose refers to"); v11 4.2.2 is a short intervention-model rationale that itself says "with a cross reference to Section 6.2". The TransCelerate comment is on the wrong row. Do not split to 4.2.2. One carve-out: the v10 4.3 "Dose escalation will be temporarily halted..." example blocks are verbatim in v11 **4.3 Trial Stopping Rules**, so those blocks go there. v11 4.2.2 has no v10 source.

**Q2. v10 10.1.5 Data Protection: 11.8.** Text is verbatim v11 11.8 (unique identifier, "personal study-related data", "contract between sponsor and study sites specifies responsibilities... data security breaches"). M11 also separates 11.7 Data Governance (retention/records) from 11.8 Data Protection (privacy). The only v10 content that belongs in 11.7 is the record-retention bullet of 10.1.8. Likewise 10.1.7 -> 11.12 and 10.1.2 -> 11.2.1.

**Q3. Unmapped sections.**
- 10.7.3 Definition of Device Deficiency -> **9.1.4**, "part: Definition of Device Deficiency". v11 9.1.4 contains an unnumbered subheading with exactly that text, after "Definition of Medical Device AE and ADE" and "Definition of Medical Device SAE, SADE and USADE". Confidence official-corrected (TransCelerate had no row).
- 8.4.6 Cardiovascular and Death Events -> **no v11 target (drop)**. The v10 heading is an empty placeholder: no instruction text and no example text (added in v03 as a locator; CPT v09 renumbered it). M11 Step 4 has no counterpart (no mention of cardiovascular/death events in 9.x) and v11 dropped it. If a real protocol has content here, recommend a review flag with default 9.2.5 (the v10 SAE regulatory-reporting note says events that are potential efficacy endpoints, eg MACE, "should be detailed in Section 8.4.7", which is 9.2.5) and 9.2.4 as the alternative when the events are adjudicated AESI. Confidence inferred-weak: needs a human decision, do not move automatically.

**Q7. Library content (contraception, liver) in a v10 protocol: move the protocol's own text, do not replace it with v11 library text.** Evidence: (a) the v11 core template has only "See participant libraries for common text to include here" under 5.4.1/5.4.2 and 12.4, and the v10 core template says the same under Appendix 4/6, so the text in a v10 protocol is whatever library text the sponsor inserted and possibly edited; (b) the libraries state "All language proposed in this Library... may be modified as the user sees fit", and the v10 Appendix 4/6 text can be sponsor-tailored (decision tree outcomes, CBP/NCBP definitions, local requirements); replacing it silently destroys the chain of custody; (c) the V010 Patient library (M11-CPT) is already numbered to the v11 sections (5.4, 7.1.1.1, 7.1.1.2, 8.4.5, 12.4.1-12.4.3), so the destination slots are unambiguous. Recommended rules: 10.4.1 -> 5.4.1, 10.4.2 -> 5.4.2, 10.6 -> 12.4 (with 7.1.1 liver stopping criteria -> 7.1.1.1 via 7.1.1), `change_type: library`. Optional later feature: flag when the moved text differs from the shipped v11 library text so the user can choose to refresh it. Related: v10 inclusion criteria ("Sex and Contraception/Barrier Requirements", 5.1 -> 5.2) also reference the contraception decision tree and move with the criteria, not with Appendix 4.

## 3. v11 sections with no v10 source (confirmed/corrected list)

Truly no source: 4.2.2, 4.2.4, 4.2.5, 4.2.6, 4.2.7, 6.3 (administration; 6.3.1 does have a source), 6.9.3, 9.1.3, 9.4, 10.3, 11.2.2, 11.5, 11.6, 11.10, plus title-page extras ([Sponsor Confidentiality Statement], [Version Number], [Version Date], Original Protocol [derivable], Local Sponsor Name/Address, Current Amendment enrolment/reason/impact cells). Heading-only containers whose children have sources: 2.1, 5.4, 6.6, 6.7, 11.2, 11.9. Weak optional partial: 6.9.1 (v10 6.1 background rows).

Removed from the md list (they do have a v10 source): 2.2.1, 2.2.2, 4.2.1 (part), 4.2.3 (weak part), 4.3 (part), 5.1 (part), 6.2, 6.7.4 (part), 7.1.1, 8.1, 8.6.3, 9.3.2 (part), 11.2.1, 11.3.1 (part), 11.3.2 (part), 11.8, 11.12. Also 9.2.1.1 and 5.8 receive content (the md body already mapped them).

## 4. Fields that legitimately land in several v11 places

Listed in `multi_landing` in the JSON. Highlights:
- **Number of Participants** (v10 1.1): v11 1.1.2 Number of Participants is the direct target (the "Note: Enrolled means..." paragraph is verbatim). It is a summary of v11 4.1 ("the expected number of participants") and 10.11 (v10 9.9), so the 1.1.2 value should be derived from or checked against 10.11, not treated as an independent source.
- Data Monitoring/Other Committee: 1.1.2 Committees field + full text at 11.4 (v10 10.1.6).
- Study Arms and Duration: 1.1.2 Duration; the same duration sentence also appears in v10 4.1 -> v11 4.1.
- Brief Summary: only study/treatment duration values to 1.1.2 Duration (structured Brief Summary removed in v11).
- Title-page Protocol Title structured fields (intervention model, blinding, number of arms, sex/age, condition) -> Full Title and the coded 1.1.2 table rows; Study Phase -> Trial Phase and inside Full Title; Compound/Intervention Name -> Product Code vs Product Name (classify the single v10 value), the 1.1.2 Intervention row and the section 6 table.
- Regulatory Agency Identifiers and PIP number appear on the v10 title page and in the synopsis; v11 keeps only the title-page table.
- Sponsor Name/Address -> Sponsor Name and Address and optionally Local Sponsor fields; Amendment Number/Scope/Country -> Amendment Identifier, Amendment Scope (+identifier), Original Protocol (derived).
- Body splits: 10.1.1 (11.1, 11.2.1), 10.1.3 (11.3, .1, .2), 10.1.8 (11.9.1, 11.7), 10.1.10 (4.4, 11.11), 4.3 (6.2, 4.3), 8.4.5 (9.3, 9.3.1, 9.3.2).

## 5. Uncertainties and caveats

- inferred-weak rows: TP.18b (v10 "Amendment [X]" heading and EU substantial statement -> v11 Current Amendment questions) and 8.4.6.
- Optional re-fit rows (default is the whole-section target, parts are for author choice): 4.2 -> 4.2.1/4.2.3; 9.4.1 -> 10.5.1.x; 6.1 -> 6.9 background/rescue; 7 (v10 "replacement of dropouts" sentence has no v11 home).
- 10.1 and 10 are mapped heading-to-heading as TransCelerate does; the real content moves through their children.
- v10 6.1 -> 6.9 is official-confirmed from the TransCelerate row and the Table 1 "Use" values (Rescue Medication, Background Intervention), not from a verbatim instruction match.
- Both templates carry stale cross references (eg v11 6 says "Section 6.9.4 Medical Devices", v10 text cites "Section 5.1" for contraception); these do not affect mapping.
- v11 heading texts embed their own numbers in some places (9.1.1, 10.4.1.x, 11.2.1, 11.2.2, 10.8.x, 12.4); normalise before matching titles.
- The helper constant `LABELS_WITHOUT_V11_SLOT = (Protocol Title, Brief Title, Acronym, Approval Date)` in `tests/convert_v10_title_page.py` reflects the project's current title-page docxtpl, not the CPT: the v11 template has slots for all four (Full Title, Short Title, Trial Acronym, Sponsor Approval).
- Section 12 placement of the v10 "Amendment [n]: (date)" block (10.9a) and Document History is inferred-strong from v11 12.3 instruction text.
