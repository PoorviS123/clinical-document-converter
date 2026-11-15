# Open questions from the research

0. **The TransCelerate mapping is not exact.** Items 1–3 are the errors found so far by heading comparison alone. Every row in the crosswalk still needs checking against section content (v010 vs v011 template text and M11 Template instructions).

1. **v010 4.3 Justification for Dose.** The official mapping comment sits on v011 4.2.2 "Rationale for Intervention Model", but v011 also has 6.2 "Rationale for Investigational Trial Intervention Dose and Regimen", which has no mapping comment. 6.2 looks like the better target. Possibly a misplaced comment in the TransCelerate file; decide which the converter uses, or split.

2. **v010 10.1.5 Data Protection.** Officially mapped to 11.7 Data Governance, but v011 has its own 11.8 Data Protection with no v010 source. Same question as above.

3. **Unmapped v010 sections.** 8.4.6 Cardiovascular and Death Events and 10.7.3 Definition of Device Deficiency have no target in the official mapping. Candidates: 9.4 Special Safety Situations or 9.2.4 for the first, 9.1.4 for the second. Check the M11 Step 4 Template instructions for those sections.

4. **Two M11→USDM mappings.** Local `resources/M11 to FHIR Mapping 02.xlsx` targets M11 Step 4 (19 Nov 2025) and USDM v4.0.0 "update pending". CDISC's `m11_mapping.xlsx` on DDF-RA main still targets the M11 Step 2 draft (14 Mar 2025). Where did the local file come from, and is there a newer upstream copy? The local file is named "FHIR" but maps to USDM paths; FHIR paths come from the HL7 IG mappings page instead.

5. **USDM version.** v4.0.0 (June 2025) is the latest DDF-RA tag. The local sheet says "update pending", so a USDM release after 4.0.0 may change paths. Watch the DDF-RA releases.

6. **FHIR IG is a ballot.** Clinical Study Protocol IG is 1.0.0-ballot2 on FHIR R6 ballot3. FHIR output built now will likely need changes when it publishes. Decide whether FHIR output is in scope for the first version or USDM JSON only.

7. **Library content in v010 protocols.** Contraception (5.4) and liver safety (7.1, 12.4) text now ships in the libraries (`resources/reference/transcelerate/libraries/`). A v010 protocol has this text in Appendices 4 and 6. Decide whether the converter moves the protocol's own appendix text or replaces it with the v11 library text.

8. **One code outside the M11 CT subset.** Mapping row "Identification of Source Data" (section 11.10) uses NCIt C125442. It is an active NCIt concept but is not in the 2025-12-19 M11 terminology files. The other 275 codes in the sheet are present.

9. **Input variety.** Real v010 protocols may have been authored with the eCPT (TEE) add-in, which tags content, or edited freely with renamed headings. Get a few real v010 protocols to check how often headings match the template exactly.
